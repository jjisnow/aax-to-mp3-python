# Upstream update and code audit — 2026-10-01 UTC

## Upstream comparison

Fork baseline: `f4ea3a6` on `jjisnow/aax-to-mp3-python/master`.
Upstream: [OndrejSkalicka/aax-to-mp3-python](https://github.com/OndrejSkalicka/aax-to-mp3-python),
`05c4c0f` (2018-12-28).

The fork was three commits behind upstream and had no unique commits. Two
upstream commits merge the fork's existing improvements; the third documents
Python 3.6 as the minimum. Upstream was fast-forwarded locally before the audit.
The updated converter now requires Python 3.10+, documented in the README.
No newer upstream implementation changes were available at audit time.

## Findings and fixes

| Severity | Finding | Resolution |
| --- | --- | --- |
| High | Arguments and FFmpeg commands printed activation bytes | Removed debug dumps, redacted keys from tool errors, added environment-key input |
| High | FFmpeg `-y` silently overwrote existing chapter files | Preflight all outputs; explicit `--overwrite`; atomic no-clobber publication by default |
| Medium | Failed conversion could leave a partial final MP3 or damage an existing one | Encode into same-directory temporary files; publish only successful, nonempty outputs; clean up on errors and interrupts |
| Medium | FFprobe was not supplied activation bytes | Pass the same key to both tools |
| Medium | Missing chapter tags or titles crashed conversion; chapterless input produced nothing | Default titles; whole-book conversion when a valid duration is available |
| Medium | Unvalidated timestamps could create incorrect output | Validate all timestamps and reject non-finite, negative, reversed and overlapping intervals before encoding |
| Medium | Subprocess failures produced tracebacks containing command arguments | Controlled tool errors, redacted diagnostics and nonzero CLI exit codes |
| Low | Each chapter decoded from the start of the book | Seek before input and encode for the chapter duration |
| Low | Audio selection, track metadata and output bitrate were implicit | Explicit first-audio-stream mapping, track tags, source metadata preservation and configurable bitrate |
| Low | Filenames beginning with a hyphen could be interpreted as options | Resolve local input and destination paths; retain shell-free argument lists |
| Low | Obsolete FFmpeg download link and insufficient setup/error guidance | Official download link, current setup, options, limitations and test instructions |
| Low | No regression coverage | Standard-library unit tests and real FFmpeg integration coverage |

## Dependencies and references

There are no external Python dependencies or lockfiles to update. Runtime
FFmpeg/FFprobe versions are installed by the user, not vendored here. Relevant
primary references:

- [FFprobe JSON output and chapter inspection](https://ffmpeg.org/ffprobe.html)
- [FFmpeg seeking, duration, stream selection and metadata](https://ffmpeg.org/ffmpeg.html)
- [FFmpeg MOV/AAX activation bytes](https://ffmpeg.org/ffmpeg-formats.html#mov)
- [MP3 encoder documentation](https://ffmpeg.org/ffmpeg-codecs.html#libmp3lame)

## Validation and remaining limits

Tested locally with Python 3.12 and FFmpeg/FFprobe 6.1.1 on Linux. Real generated
AAC/M4B input verifies chapter lengths and ID3 title, album, artist and track
metadata. Safety tests exercise failed encodes, overwrite refusal, old-output
preservation and concurrent destination creation.

Encrypted AAX decryption has not been exercised: no user audiobook or activation
key was available. Native Windows execution has not been exercised. Test the
CLI with one owned AAX file before a large batch. Process arguments still expose
the activation bytes to sufficiently privileged process inspection; redaction
protects script diagnostics, not operating-system visibility. Chapter publication
is atomic individually, not across the complete book. POSIX no-clobber publication
requires hard-link support. The tool does not support AAXC or preserve cover art.
