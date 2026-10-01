"""Convert a local audiobook to chapter MP3s using FFmpeg and FFprobe."""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from decimal import Decimal, InvalidOperation
from pathlib import Path


class ConversionError(Exception):
    """An expected input, metadata or external-tool failure."""


def _tool(name):
    executable = shutil.which(name)
    if not executable:
        raise ConversionError(f'{name} not found; install FFmpeg and add its bin directory to PATH')
    return executable


def _activation(value):
    if value is not None and not re.fullmatch(r'[0-9a-fA-F]{8}', value):
        raise ConversionError('Activation bytes must be exactly eight hexadecimal characters')
    return value


def _run(cmd, activation_bytes=None):
    """Never include the command (which contains a secret) in diagnostics."""
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8',
                                errors='replace', check=False)
    except OSError as exc:
        raise ConversionError(f'Unable to run {Path(cmd[0]).stem}') from exc
    if result.returncode:
        detail = result.stderr.strip()
        if activation_bytes:
            detail = re.sub(re.escape(activation_bytes), '[REDACTED]', detail, flags=re.I)
        raise ConversionError(f'{Path(cmd[0]).stem} failed (exit {result.returncode}): {detail}')
    return result.stdout


def get_chapters(input, activation_bytes=None):
    """Read chapter timestamps and duration, supplying the AAX key to FFprobe."""
    activation_bytes = _activation(activation_bytes)
    cmd = [_tool('ffprobe'), '-v', 'error', '-show_chapters', '-show_format',
           '-of', 'json']
    if activation_bytes:
        cmd.extend(['-activation_bytes', activation_bytes])
    cmd.extend(['-i', str(Path(input).resolve())])
    try:
        data = json.loads(_run(cmd, activation_bytes))
    except json.JSONDecodeError as exc:
        raise ConversionError('FFprobe returned invalid JSON') from exc
    if not isinstance(data, dict):
        raise ConversionError('FFprobe returned invalid metadata')
    return data


def _timestamp(value):
    try:
        number = Decimal(str(value))
    except InvalidOperation as exc:
        raise ConversionError('Invalid chapter timestamp or duration') from exc
    if not number.is_finite() or number < 0:
        raise ConversionError('Chapter timestamps must be finite and non-negative')
    return number


def _chapters(data, fallback_title):
    """Validate every chapter before writing; use a whole-book fallback if absent."""
    raw = data.get('chapters', [])
    if not isinstance(raw, list):
        raise ConversionError('Invalid chapter list')
    if not raw:
        format_data = data.get('format') or {}
        if not isinstance(format_data, dict):
            raise ConversionError('Invalid format metadata')
        raw = [{'start_time': '0', 'end_time': format_data.get('duration'),
                'tags': {'title': fallback_title}}]
    chapters = []
    previous_end = Decimal(0)
    for index, chapter in enumerate(raw, 1):
        if not isinstance(chapter, dict):
            raise ConversionError(f'Invalid chapter {index}')
        start = _timestamp(chapter.get('start_time'))
        end = _timestamp(chapter.get('end_time'))
        if end <= start or start < previous_end:
            raise ConversionError(f'Chapter {index} has invalid or overlapping times')
        tags = chapter.get('tags') or {}
        if not isinstance(tags, dict):
            raise ConversionError(f'Invalid tags for chapter {index}')
        title = str(tags.get('title') or f'Chapter {index}')
        chapters.append((start, end - start, title))
        previous_end = end
    return chapters


def parse_chapters(chapters, input, activation_bytes, album, *, output_dir='.',
                   bitrate='128k', overwrite=False):
    """Encode into temporary files; publish only successfully completed chapters."""
    source = Path(input).resolve()
    if not source.is_file():
        raise ConversionError('Input must be an existing local file')
    activation_bytes = _activation(activation_bytes)
    if bitrate not in {'32k', '40k', '48k', '56k', '64k', '80k', '96k', '112k',
                       '128k', '160k', '192k', '224k', '256k', '320k'}:
        raise ConversionError('Unsupported MP3 bitrate')
    ffmpeg = _tool('ffmpeg')
    validated = _chapters(chapters, source.stem)
    destination = Path(output_dir).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    outputs = [destination / f'{source.stem}_{i}.mp3'
               for i in range(1, len(validated) + 1)]
    for output in outputs:
        if output.resolve() == source:
            raise ConversionError('Output would replace the input file')
        if output.exists() or output.is_symlink():
            if not overwrite or not output.is_file() or output.is_symlink():
                raise ConversionError(f'Output already exists: {output}; use --overwrite for regular files')

    for index, ((start, duration, title), output) in enumerate(zip(validated, outputs), 1):
        # Same-directory temporary files allow atomic publication on the same volume.
        fd, temporary = tempfile.mkstemp(prefix='.aax-convert-', suffix='.mp3', dir=destination)
        os.close(fd)
        temporary = Path(temporary)
        try:
            cmd = [ffmpeg, '-hide_banner', '-loglevel', 'error', '-nostdin', '-y']
            if activation_bytes:
                cmd.extend(['-activation_bytes', activation_bytes])
            # Input seeking avoids decoding from the beginning for every chapter.
            cmd.extend(['-ss', str(start), '-i', str(source), '-t', str(duration),
                        '-map', '0:a:0', '-map_metadata', '0', '-map_chapters', '-1',
                        '-metadata', f'title={title}', '-metadata', f'track={index}/{len(validated)}'])
            if album is not None:
                cmd.extend(['-metadata', f'album={album}'])
            cmd.extend(['-c:a', 'libmp3lame', '-b:a', bitrate, '-id3v2_version', '3',
                        '-vn', str(temporary)])
            _run(cmd, activation_bytes)
            if not temporary.stat().st_size:
                raise ConversionError('FFmpeg produced an empty output')
            if overwrite:
                os.replace(temporary, output)
            elif os.name == 'nt':
                # Windows rename refuses an existing destination, even on FAT/exFAT.
                os.rename(temporary, output)
            else:
                # Atomic no-clobber publication, including a concurrently created output.
                os.link(temporary, output)
            print(f'Created: {output}')
        finally:
            temporary.unlink(missing_ok=True)
    return outputs


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('-i', '--input', required=True, help='Local AAX audiobook file')
    parser.add_argument('-a', '--activation-bytes',
                        help='Eight hexadecimal characters; defaults to AAX_ACTIVATION_BYTES')
    parser.add_argument('--album', help='Override album tag; otherwise preserve source metadata')
    parser.add_argument('-o', '--output-dir', default='.', help='Destination directory (default: current directory)')
    parser.add_argument('--bitrate', default='128k', help='MP3 bitrate (default: 128k)')
    parser.add_argument('--overwrite', action='store_true', help='Replace existing chapter MP3s')
    args = parser.parse_args(argv)
    try:
        source = Path(args.input).resolve()
        if not source.is_file():
            raise ConversionError('Input must be an existing local file')
        key = _activation(args.activation_bytes if args.activation_bytes is not None
                          else os.environ.get('AAX_ACTIVATION_BYTES'))
        if not key:
            raise ConversionError('Supply --activation-bytes or set AAX_ACTIVATION_BYTES')
        _tool('ffmpeg')
        chapters = get_chapters(source, key)
        parse_chapters(chapters, source, key, args.album, output_dir=args.output_dir,
                       bitrate=args.bitrate, overwrite=args.overwrite)
    except (ConversionError, OSError) as exc:
        print(f'Error: {exc}', file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print('Conversion interrupted', file=sys.stderr)
        return 130
    return 0


if __name__ == '__main__':
    sys.exit(main())
