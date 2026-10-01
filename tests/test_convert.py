"""Regression checks for unsafe output, metadata and tool failure handling."""
import contextlib
import io
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import convert


class ConverterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'book.aax'
        self.source.write_bytes(b'input')
        self.data = {'chapters': [{'start_time': '1.25', 'end_time': '2.75'}]}

    def encode(self, data=None, **kwargs):
        with patch('convert._tool', return_value='ffmpeg'), contextlib.redirect_stdout(io.StringIO()):
            return convert.parse_chapters(data or self.data, self.source, 'abcdef12', None,
                                          output_dir=self.root, **kwargs)

    def test_key_validation_and_redaction(self):
        with self.assertRaises(convert.ConversionError):
            convert._activation('secret')
        result = subprocess.CompletedProcess([], 1, '', 'Bad key ABCDEF12')
        with patch('convert.subprocess.run', return_value=result):
            with self.assertRaises(convert.ConversionError) as error:
                convert._run(['ffmpeg', '-activation_bytes', 'abcdef12'], 'abcdef12')
        self.assertNotIn('ABCDEF12', str(error.exception))
        self.assertIn('[REDACTED]', str(error.exception))

    def test_probe_passes_key_and_handles_invalid_json(self):
        with patch('convert._tool', return_value='ffprobe'), patch('convert._run', return_value='{}') as run:
            self.assertEqual(convert.get_chapters(self.source, 'abcdef12'), {})
        cmd = run.call_args.args[0]
        self.assertEqual(cmd[cmd.index('-activation_bytes') + 1], 'abcdef12')
        with patch('convert._tool', return_value='ffprobe'), patch('convert._run', return_value='bad'):
            with self.assertRaises(convert.ConversionError):
                convert.get_chapters(self.source)

    def test_invalid_times_rejected_before_encoding(self):
        for start, end in [('NaN', '2'), ('-1', '2'), ('2', '2'), ('3', '2'), ('0', 'Infinity'), ('0', None)]:
            with self.subTest(start=start, end=end), patch('convert._run') as run:
                with self.assertRaises(convert.ConversionError):
                    self.encode({'chapters': [{'start_time': start, 'end_time': end}]})
                run.assert_not_called()
        with self.assertRaises(convert.ConversionError):
            convert._chapters({'chapters': [{'start_time': '0', 'end_time': '2'},
                                           {'start_time': '1', 'end_time': '3'}]}, 'book')

    def test_fallback_title_and_whole_book(self):
        self.assertEqual(convert._chapters(self.data, 'book')[0][2], 'Chapter 1')
        self.assertEqual(convert._chapters({'format': {'duration': '4.2'}}, 'book'),
                         [(convert.Decimal(0), convert.Decimal('4.2'), 'book')])
        with self.assertRaises(convert.ConversionError):
            convert._chapters({}, 'book')

    @staticmethod
    def fake_encode(cmd, key):
        Path(cmd[-1]).write_bytes(b'complete')
        return ''

    def test_command_duration_metadata_and_safe_paths(self):
        self.source = self.root / '-quoted " ; book.aax'
        self.source.write_bytes(b'input')
        with patch('convert._run', side_effect=self.fake_encode) as run:
            outputs = self.encode()
        cmd = run.call_args.args[0]
        self.assertLess(cmd.index('-ss'), cmd.index('-i'))
        self.assertEqual(cmd[cmd.index('-t') + 1], '1.50')
        self.assertIn('title=Chapter 1', cmd)
        self.assertIn('track=1/1', cmd)
        self.assertEqual(cmd[cmd.index('-map') + 1], '0:a:0')
        self.assertEqual(outputs[0].read_bytes(), b'complete')
        self.assertEqual(list(self.root.glob('.aax-convert-*')), [])

    def test_existing_output_protected_and_explicit_overwrite(self):
        output = self.root / 'book_1.mp3'
        output.write_bytes(b'old')
        with patch('convert._run') as run:
            with self.assertRaises(convert.ConversionError):
                self.encode()
            run.assert_not_called()
        with patch('convert._run', side_effect=self.fake_encode):
            self.encode(overwrite=True)
        self.assertEqual(output.read_bytes(), b'complete')

    def test_failed_conversion_preserves_old_output_and_cleans_temp(self):
        output = self.root / 'book_1.mp3'
        output.write_bytes(b'old')
        def fail(cmd, key):
            Path(cmd[-1]).write_bytes(b'partial')
            raise convert.ConversionError('failed')
        with patch('convert._run', side_effect=fail):
            with self.assertRaises(convert.ConversionError):
                self.encode(overwrite=True)
        self.assertEqual(output.read_bytes(), b'old')
        self.assertEqual(list(self.root.glob('.aax-convert-*')), [])

    def test_concurrent_output_is_not_overwritten(self):
        output = self.root / 'book_1.mp3'
        def race(cmd, key):
            self.fake_encode(cmd, key)
            output.write_bytes(b'other process')
        with patch('convert._run', side_effect=race):
            with self.assertRaises(FileExistsError):
                self.encode()
        self.assertEqual(output.read_bytes(), b'other process')
        self.assertEqual(list(self.root.glob('.aax-convert-*')), [])

    def test_cli_missing_input_and_bad_key_are_clean_errors(self):
        for args in [['-i', str(self.root / 'missing'), '-a', 'abcdef12'],
                     ['-i', str(self.source), '-a', 'private-key']]:
            with contextlib.redirect_stderr(io.StringIO()) as stderr:
                self.assertEqual(convert.main(args), 1)
            self.assertNotIn('Traceback', stderr.getvalue())
            self.assertNotIn('private-key', stderr.getvalue())

    def test_all_outputs_preflighted(self):
        data = {'chapters': [{'start_time': '0', 'end_time': '1'},
                             {'start_time': '1', 'end_time': '2'}]}
        (self.root / 'book_2.mp3').write_bytes(b'old')
        with patch('convert._run') as run:
            with self.assertRaises(convert.ConversionError):
                self.encode(data)
            run.assert_not_called()
        self.assertFalse((self.root / 'book_1.mp3').exists())

    def test_interruption_cleans_partial_output(self):
        def interrupt(cmd, key):
            Path(cmd[-1]).write_bytes(b'partial')
            raise KeyboardInterrupt
        with patch('convert._run', side_effect=interrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.encode()
        self.assertFalse((self.root / 'book_1.mp3').exists())
        self.assertEqual(list(self.root.glob('.aax-convert-*')), [])

    def test_symlink_output_refused_even_with_overwrite(self):
        target = self.root / 'target'
        target.write_bytes(b'keep')
        output = self.root / 'book_1.mp3'
        try:
            output.symlink_to(target)
        except OSError:
            self.skipTest('Symlink creation not available')
        with patch('convert._run') as run:
            with self.assertRaises(convert.ConversionError):
                self.encode(overwrite=True)
            run.assert_not_called()
        self.assertEqual(target.read_bytes(), b'keep')

    def test_environment_key(self):
        with patch.dict('os.environ', {'AAX_ACTIVATION_BYTES': 'abcdef12'}), \
                patch('convert._tool'), patch('convert.get_chapters', return_value={}) as probe, \
                patch('convert.parse_chapters'):
            self.assertEqual(convert.main(['-i', str(self.source)]), 0)
        self.assertEqual(probe.call_args.args[1], 'abcdef12')


@unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'FFmpeg tools required')
class FFmpegIntegrationTests(unittest.TestCase):
    def test_real_chapter_durations_and_tags(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            metadata = root / 'chapters.txt'
            metadata.write_text(';FFMETADATA1\nalbum=Original album\nartist=Author\n'
                                '[CHAPTER]\nTIMEBASE=1/1000\nSTART=0\nEND=1000\ntitle=First\n'
                                '[CHAPTER]\nTIMEBASE=1/1000\nSTART=1000\nEND=3000\ntitle=Second\n')
            source = root / 'book.m4b'
            subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i',
                            'sine=frequency=440:duration=3', '-f', 'ffmetadata', '-i', str(metadata),
                            '-map_metadata', '1', '-map_chapters', '1', '-c:a', 'aac', str(source)], check=True)
            data = convert.get_chapters(source)
            with contextlib.redirect_stdout(io.StringIO()):
                outputs = convert.parse_chapters(data, source, None, None, output_dir=root)
            self.assertEqual(len(outputs), 2)
            for i, (output, duration) in enumerate(zip(outputs, [1, 2]), 1):
                info = convert.get_chapters(output)['format']
                self.assertAlmostEqual(float(info['duration']), duration, delta=0.1)
                self.assertEqual(info['tags']['album'], 'Original album')
                self.assertEqual(info['tags']['artist'], 'Author')
                self.assertEqual(info['tags']['title'], ['First', 'Second'][i-1])
                self.assertEqual(info['tags']['track'], f'{i}/2')
            # Exercise real chapterless input and the album override as well.
            source = root / 'plain.wav'
            subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i',
                            'sine=frequency=220:duration=1', str(source)], check=True)
            with contextlib.redirect_stdout(io.StringIO()):
                outputs = convert.parse_chapters(convert.get_chapters(source), source, None,
                                                 'Override', output_dir=root)
            info = convert.get_chapters(outputs[0])['format']
            self.assertAlmostEqual(float(info['duration']), 1, delta=0.1)
            self.assertEqual(info['tags']['album'], 'Override')
            self.assertEqual(info['tags']['title'], 'plain')


if __name__ == '__main__':
    unittest.main()
