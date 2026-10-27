# First extraction with curl

In this tutorial you start MES, extract metadata from a file with `curl`, and read the result. It takes about ten minutes.

## Watch it first

The recording below shows the whole flow with `metaflac` as the example tool.

<script async id="asciicast-HyioKG5kRr8lRdvF" src="https://asciinema.org/a/HyioKG5kRr8lRdvF.js"></script>

If the player does not load (it needs <https://asciinema.org> to be reachable), see [the plain-text output](#output-of-the-recording) below.

## 1. Start the service

From the repository root:

```bash
cd service
cp .env.example .env     # then set DJANGO_SECRET_KEY to any long random string
docker compose up --build -d
```

Check that it answers:

```bash
curl -s http://localhost:8000/api/v1/tools/
```

You get a list of the configured tools; `exiftool` and `mediainfo` are enabled by default.

## 2. Extract metadata

Pick any file, for example `photo.jpg`:

```bash
curl -s -F file=@photo.jpg http://localhost:8000/api/v1/extract/
```

The response is JSON. The parts to look at:

- `tool_resolution`: how the tools were chosen (`selection_method`).
- `results`: one entry per tool, each with its `status` and the unmodified `raw_output`.
- `errors` and `warnings`: problems, at the top level and per tool.

## 3. Ask for XML

```bash
curl -s -H 'Accept: application/xml' -F file=@photo.jpg http://localhost:8000/api/v1/extract/
```

The XML has the same content, and a schema at `/api/v1/schema/result.xsd`.

## 4. Run it as an asynchronous job

```bash
curl -s -i -F file=@photo.jpg http://localhost:8000/api/v1/jobs/
curl -s http://localhost:8000/api/v1/jobs/<job_id>/
curl -s http://localhost:8000/api/v1/jobs/<job_id>/result/
```

The first call returns `202 Accepted` with a `job_id`. Poll the second until `status` is `succeeded`, then fetch the result, which equals the synchronous one.

## Next steps

- [Reference: REST API](../reference/rest-api.md)
- [Enable more tools](../how-to/optional-tools.md)
- [Extract several files at once](../how-to/batch-extraction.md)

## Output of the recording

??? example "Plain-text output"

    ```console
    $ curl -s http://localhost:8000/api/v1/tools/ | python3 -I -c '...'
    exiftool enabled=True
    mediainfo enabled=True
    ffprobe enabled=True
    imagemagick enabled=True
    metaflac enabled=True
    file_scraper enabled=True
    tika enabled=True

    $ curl -s http://localhost:8000/api/v1/limits/
    {"max_upload_bytes":536870912,"max_files_per_request":10,"async_enabled":true}

    $ curl -s http://localhost:8000/api/v1/local-rules/
    {"rules":[{"name":"flac-audio","puids":["fmt/279"],"extensions":["flac"],"tools":["metaflac"],"match":"any"}]}

    $ curl -s -F file=@sample.flac http://localhost:8000/api/v1/extract/ | python3 -I summarise.py
    file: sample.flac  PUID: fmt/279  MIME: audio/flac
    selected by: local_rule_via_ffis  rules: flac-audio
      exiftool   success
      metaflac   success
    errors: 0  warnings: 0

    $ curl -s -F file=@sample.flac http://localhost:8000/api/v1/extract/ | python3 -I metaflac.py
    metaflac --list ...
      sample_rate: 44100 Hz
      channels: 1
      bits-per-sample: 16
      total samples: 4096
        comment[0]: TITLE=MES demo tone
        comment[1]: ARTIST=EOSC EDEN

    $ curl -s -H 'Accept: application/xml' -F file=@sample.flac http://localhost:8000/api/v1/extract/ | head -c 300
    <?xml version='1.0' encoding='utf-8'?>
    <extractionResult xmlns="urn:eden:mes:result:1" ...

    $ curl -s http://localhost:8000/api/v1/jobs/<job_id>/
    {"job_id":"<job_id>","status":"succeeded","created_at":"...","errors":[]}
    $ curl -s http://localhost:8000/api/v1/jobs/<job_id>/result/ | python3 -I summarise.py
    file: sample.flac  PUID: fmt/279  MIME: audio/flac
    selected by: local_rule_via_ffis  rules: flac-audio
      exiftool   success
      metaflac   success
    errors: 0  warnings: 0
    ```
