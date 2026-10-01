# AAX to MP3 in Python

Convert a local audiobook into one MP3 per chapter using FFmpeg. Files are named
`Book_1.mp3`, `Book_2.mp3`, and so on. If the audiobook has no chapter markers,
the script writes one MP3 using the reported duration.

## Requirements

- Python 3.10 or newer. No third-party Python packages are required.
- [FFmpeg and FFprobe](https://ffmpeg.org/download.html) on your `PATH`, with
  the `libmp3lame` encoder and Audible AAX support. Keep FFmpeg updated: it
  processes the media file itself, while this script only coordinates the tools.
- Your audiobook's activation bytes: exactly **eight hexadecimal characters**.
  The script does not retrieve them. The historical
  [inAudible-NG tables project](https://github.com/inAudible-NG/tables/) is a
  separate tool, not a runtime dependency. Convert content you own and are
  authorised to convert. This script targets AAX, not AAXC.

On Windows, extract an FFmpeg build linked from the official download page and
add its `bin` directory to `PATH`. Open a new terminal afterwards. Verify:

```text
python --version
ffmpeg -version
ffprobe -version
```

## Convert

From the repository directory:

```text
python convert.py -i "The Tower of the Swallow.aax" -a 01234567
```

Use your actual activation bytes in place of the example. The default destination
is the current directory; the default MP3 bitrate is 128 kbit/s.

```text
python convert.py -i "Book.aax" -a 01234567 --output-dir "converted" --bitrate 64k --album "My audiobook"
```

Supported bitrates: `32k`, `40k`, `48k`, `56k`, `64k`, `80k`, `96k`, `112k`,
`128k`, `160k`, `192k`, `224k`, `256k`, `320k`. Omitting `--album` preserves
the source album tag. Source metadata is copied, chapter titles become MP3
titles, and each MP3 receives a track number. Missing chapter titles fall back
to `Chapter N`. Cover artwork is not copied.

Existing outputs cause an error **before any chapter is encoded**. Use
`--overwrite` to replace regular output files explicitly. Each chapter is
encoded into a temporary file in the destination and only published after a
successful FFmpeg exit. Failed or interrupted chapters are cleaned up; completed
chapters remain. Replacement is per chapter, not a transaction for the entire
book. Abrupt process termination or power loss can leave `.aax-convert-*` files;
you can remove these once no conversion is running. On POSIX, default no-clobber
publication requires a filesystem supporting hard links; Windows uses rename.

### Avoid putting the key in shell history

Set `AAX_ACTIVATION_BYTES`, then omit `-a`:

PowerShell:

```powershell
$env:AAX_ACTIVATION_BYTES = Read-Host "Activation bytes" -MaskInput
python convert.py -i "Book.aax" --output-dir "converted"
Remove-Item Env:AAX_ACTIVATION_BYTES
```

`-MaskInput` requires PowerShell 7.1 or newer. On Windows PowerShell 5.1,
`Read-Host "Activation bytes"` works but displays the entered value.

Bash:

```bash
read -rsp "Activation bytes: " AAX_ACTIVATION_BYTES
export AAX_ACTIVATION_BYTES
python convert.py -i "Book.aax" --output-dir "converted"
unset AAX_ACTIVATION_BYTES
```

The script does not print the key and redacts it from FFmpeg/FFprobe errors.
FFmpeg still receives it as a process argument, so it may be visible to users
or monitoring tools able to inspect processes. Keep keys out of shared logs.

## Troubleshooting

- **Tool not found:** both `ffmpeg` and `ffprobe` must be on `PATH`.
- **Activation error:** check the eight-character key and AAX compatibility.
- **Invalid chapter times:** the script rejects missing, non-finite, negative,
  reversed or overlapping times rather than silently producing incorrect audio.
- **No duration:** a chapterless file needs a duration reported by FFprobe.
- **Encoder unavailable:** use an FFmpeg build including `libmp3lame`.
- **Permission/publication error:** use a writable destination on a supported
  filesystem and check that no other converter is writing the same outputs.

Errors exit with status 1; Ctrl+C exits with status 130. Use
`python convert.py --help` for all options.

## Development and verification

```text
python -m unittest discover -s tests -v
python -m compileall -q convert.py tests
```

Unit tests cover metadata validation, secret redaction, overwrite protection,
failure cleanup and concurrent output creation. Integration tests generate a
small chaptered audiobook and verify real MP3 durations and tags; they skip if
FFmpeg/FFprobe are unavailable. They do not require a private audiobook or key.
See [AUDIT.md](AUDIT.md) for upstream provenance, findings and testing limits.
