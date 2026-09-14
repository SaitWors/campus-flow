"""Apply small, asserted adapters to the pinned translator image.

Campus Flow translates titles of at most 120 characters, each as one segment.
Argos 1.11 declares ChunkType.NONE but leaves it unimplemented. Implement that
case locally so sentence detection never downloads an unrelated ONNX model.
"""
from pathlib import Path
from importlib.util import find_spec


def replace_once(path, old, new):
    source = path.read_text()
    if source.count(old) != 1:
        raise RuntimeError('Pinned translator source changed: ' + str(path))
    path.write_text(source.replace(old, new))


def main():
    replace_once(Path('libretranslate/init.py'),
                 'len(package.get_installed_packages()) < 2',
                 'len(package.get_installed_packages()) < 1')
    source = Path(find_spec('argostranslate').origin).parent / 'translate.py'
    replace_once(source, 'class PackageTranslation(ITranslation):', '''
class TitleSentencizer:
    def __init__(self, pkg):
        pass

    def split_sentences(self, text):
        return [text] if text else []


class PackageTranslation(ITranslation):'''.lstrip())
    replace_once(source, '        Sentencizer = None',
                 '        Sentencizer = TitleSentencizer if settings.chunk_type == settings.ChunkType.NONE else None')


if __name__ == '__main__':
    main()
