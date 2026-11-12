# Runtime dependency licenses

The `LICENSE` in this repository covers original MEDRE-authored adapter source
(MIT), **not** the official SimpleX client it drives.

- `simplex-chat==7.1.3`: AGPL-3.0-only — [release metadata](https://pypi.org/project/simplex-chat/).
  The native library downloaded on first initialization carries its own
  upstream AGPL license in the [simplex-chat repository](https://github.com/simplex-chat/simplex-chat).
  This disclosure identifies the principal client and native project terms;
  it does not replace the notices of other bundled native components.

Separate repositories and Python package entry points do not waive these
terms. Nor does a separate process automatically solve licensing questions.
Review the actual packaging and operational use case with qualified counsel
before asserting compatibility or distribution rights.

See [MEDRE licensing](https://github.com/jeremiah-k/medre/blob/main/docs/dev/licensing.md).
