# Third-Party Notices

Last reviewed: 2026-10-02.

The root [LICENSE](./LICENSE) covers only original contributions. Third-party
software, fonts, models, data, and services retain their own terms.

## Bundled web fonts

- Packages: @fontsource/cormorant, @fontsource/montserrat and @fontsource/plus-jakarta-sans.
- License: SIL Open Font License 1.1.
- Sources: [Fontsource Cormorant](https://fontsource.org/fonts/cormorant) and
  [Fontsource Montserrat](https://fontsource.org/fonts/montserrat) and
  [Fontsource Plus Jakarta Sans](https://fontsource.org/fonts/plus-jakarta-sans).
- Distribution: retain the applicable font license and attribution when
  publishing a frontend bundle or desktop package containing these font files.

## Application dependencies and external services

The frontend and backend manifests declare additional dependencies with their
own license metadata. RunningHub, Taobao and weather integrations are external
services; their API, data, model, and account terms are not granted by this
repository's LICENSE. The wedding Ark and DashScope adapters likewise require
their own account/model terms and portrait permissions.

OpenCV 4.x core uses [Apache 2.0](https://github.com/opencv/opencv/blob/4.x/LICENSE);
the Python packaging wrapper uses [MIT](https://github.com/opencv/opencv-python/blob/4.x/LICENSE.txt).
Retain wheel-provided third-party licenses as well; these are not all covered
by the wrapper's MIT notice.

FFmpeg's applicable LGPL/GPL terms depend on the delivered build and enabled
components; inspect its configuration and follow [FFmpeg licensing](https://ffmpeg.org/legal.html)
when distributing binaries/images. The task-local Windows test binary is not
bundled in Git.

No commercial music is supplied. An operator-provided catalog must have actual
customer embedded-delivery and wedding-playback rights. Synthetic acceptance
tones authorize no customer delivery; model/API terms and music rights are
separate from the root source license.
