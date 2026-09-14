# Third-party notices

The interface uses Lucide SVG icons from the installed lucide-react package. Lucide uses the ISC license and includes some Feather-derived icons under MIT. The exact installed license text is reproduced below.

Sources: [Lucide](https://lucide.dev/), [Lucide license](https://lucide.dev/license), [Feather](https://github.com/feathericons/feather).

The calendar layout, application styles and simple favicon were created for this project. No MTUCI logo, official timetable or unlicensed design template is bundled. System fonts are used; no remote fonts or external image services are required.

Other runtime/build dependencies retain their own package licenses: React / React DOM (MIT), Vite (MIT), TypeScript (Apache-2.0), FastAPI (MIT), Uvicorn (BSD-3-Clause), SQLAlchemy (MIT), HTTPX (BSD-3-Clause), psycopg (LGPL-3.0), Argon2-cffi (MIT). Container components PostgreSQL, Nginx, Python, Node.js and their base OS carry their upstream licenses. See the installed distributions and upstream repositories for complete notices; this list does not relicense their code.

## Lucide package license

ISC License

Copyright (c) for portions of Lucide are held by Cole Bemis 2013-2022 as part of Feather (MIT). All other copyright (c) for Lucide are held by Lucide Contributors 2022.

Permission to use, copy, modify, and/or distribute this software for any
purpose with or without fee is hereby granted, provided that the above
copyright notice and this permission notice appear in all copies.

THE SOFTWARE IS PROVIDED "AS IS" AND THE AUTHOR DISCLAIMS ALL WARRANTIES
WITH REGARD TO THIS SOFTWARE INCLUDING ALL IMPLIED WARRANTIES OF
MERCHANTABILITY AND FITNESS. IN NO EVENT SHALL THE AUTHOR BE LIABLE FOR
ANY SPECIAL, DIRECT, INDIRECT, OR CONSEQUENTIAL DAMAGES OR ANY DAMAGES
WHATSOEVER RESULTING FROM LOSS OF USE, DATA OR PROFITS, WHETHER IN AN
ACTION OF CONTRACT, NEGLIGENCE OR OTHER TORTIOUS ACTION, ARISING OUT OF
OR IN CONNECTION WITH THE USE OR PERFORMANCE OF THIS SOFTWARE.


## Local machine translation

LibreTranslate v1.9.6 runs as a separate unmodified service. License: GNU AGPL-3.0; corresponding [source](https://github.com/LibreTranslate/LibreTranslate/tree/v1.9.6) and [full license](https://github.com/LibreTranslate/LibreTranslate/blob/v1.9.6/LICENSE). Project-specific Docker configuration is included here. Language packages are installed from the official Argos Translate index during build; preserve notices distributed with those packages and the image when redistributing it. Models are not committed in this repository.

## PR2

- BellRing SVG is imported from the existing Lucide React dependency: [icon](https://lucide.dev/icons/bell-ring), [ISC license](https://lucide.dev/license). No remote icon CDN is used.
- Notification badge motion was adapted from the user's supplied TutCSS.css / TutReact.js, attributed there to Transitions.dev. This attribution does not assert a separate license for that supplied snippet.
- Web Push encryption uses [pywebpush](https://github.com/web-push-libs/pywebpush), MPL-2.0, and its declared dependencies. Source files of these packages are included in the installed Python distributions; this project does not modify them.
- The optional LibreTranslate AGPL-3.0 image has one documented source change in infra/translator.Dockerfile: the startup package-count condition accepts one installed direction. All project changes and the installer source are in this repository; the upstream version/source links above remain applicable.
- PWA PNG icons are project-owned geometric calendar drawings generated reproducibly by apps/web/scripts/create-icons.mjs. No university seal or external brand artwork is used.
