"""Saving uploaded files to disk for the extraction tools."""

import os


def save_upload(upload, directory, stem="upload") -> str:
    """Write `upload` into `directory` as `<stem><suffix>` and return the path.

    The original suffix is kept because some tools rely on the extension. Only the suffix of the
    uploaded name is used, so the name cannot place the file outside `directory`.
    """
    path = os.path.join(directory, f"{stem}{os.path.splitext(os.path.basename(upload.name))[1]}")
    with open(path, "wb") as handle:
        for chunk in upload.chunks():
            handle.write(chunk)
    return path
