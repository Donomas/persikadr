# Сторонние компоненты

Лицензия MIT в этом репозитории относится к исходникам Persikadr. Архив исходников содержит код и ресурсы проекта. Зависимости загружаются отдельно при установке или сборке, и на них распространяются их собственные условия.

| Компонент | Версия | Лицензия и материалы проекта |
| --- | --- | --- |
| imageio-ffmpeg | 0.6.0 | [BSD-2-Clause для Python-обёртки](https://github.com/imageio/imageio-ffmpeg/blob/v0.6.0/LICENSE) |
| FFmpeg для Windows x64 из imageio-ffmpeg | 7.1 Essentials, Gyan.dev | GPL-3.0-or-later для этого бинарника. [Сведения о сборках](https://www.gyan.dev/ffmpeg/builds/) и [условия FFmpeg](https://www.ffmpeg.org/legal.html) |
| PySide6, Shiboken6 и Qt | 6.12.0 | [Лицензии Qt for Python](https://doc.qt.io/qtforpython-6/licenses.html) и [условия распространения Qt](https://www.qt.io/development/open-source-lgpl-obligations) |
| PyInstaller | 6.22.3 | [GPL-2.0-or-later с исключением для сборок](https://pyinstaller.org/en/stable/license.html) |
| Python | 3.11 | [Лицензия Python](https://docs.python.org/3.11/license.html) |

Команда сборки из README создаёт EXE для локального использования. При публикации бинарной сборки с зависимостями нужно отдельно выполнить их условия. В частности, для включённого FFmpeg нужен доступ к полному соответствующему исходному коду сборки. Для Qt и PySide6 нужно проверить лицензии всех включённых модулей и соблюсти условия их распространения.
