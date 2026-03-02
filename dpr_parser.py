#!/usr/bin/env python3
"""
Delphi DPR Parser
Lê um arquivo .dpr, extrai todas as units da seção 'uses' que possuem
caminho explícito (formato: UnitName in 'caminho\arquivo.pas'), e copia
todos os arquivos com o mesmo nome base (ex: .pas, .dfm, .fmx, .resx, etc.)
para uma estrutura de diretórios de destino.

Uso:
    python dpr_parser.py <arquivo.dpr> <diretorio_destino> [--base-dir <dir>]

Exemplos:
    python dpr_parser.py autcom.dpr C:\saida
    python dpr_parser.py autcom.dpr /tmp/saida --base-dir D:\projetos\autcom
"""

import json
import re
import sys
import shutil
import argparse
from pathlib import Path


# Extensões adicionais a procurar além da .pas informada no .dpr
EXTRA_EXTENSIONS = [
    '.dfm',   # Delphi Form
    '.fmx',   # FireMonkey Form
    '.xfm',   # Form alternativo
    '.resx',  # Resources
    '.res',   # Resources compilados
    '.dcu',   # Compiled Unit
    '.nfm',   # Delphi .NET Form
]


def strip_block_comments(text: str) -> str:
    """Remove comentários de bloco { ... } mas mantém diretivas de compilação {$ ...}."""
    result = []
    i = 0
    while i < len(text):
        if text[i] == '{':
            # Verifica se é uma diretiva de compilação {$ ...}
            if i + 1 < len(text) and text[i + 1] == '$':
                # Mantém a diretiva — avança até o fechamento
                end = text.find('}', i)
                if end == -1:
                    result.append(text[i:])
                    break
                result.append(text[i:end + 1])
                i = end + 1
            else:
                # Comentário comum — descarta
                end = text.find('}', i)
                if end == -1:
                    break
                i = end + 1
        elif text[i:i+2] == '//':
            # Comentário de linha — descarta até o fim da linha
            end = text.find('\n', i)
            if end == -1:
                break
            i = end + 1
        else:
            result.append(text[i])
            i += 1
    return ''.join(result)


def remove_compiler_directives(text: str) -> str:
    """Remove diretivas de compilação {$IFDEF ...}, {$IFNDEF ...}, {$ENDIF}, etc."""
    return re.sub(r'\{\$[^}]*\}', '', text)


def extract_uses_section(dpr_text: str) -> str:
    """Extrai o conteúdo da seção 'uses' do .dpr."""
    # Remove comentários de bloco comuns (mantém {$...})
    clean = strip_block_comments(dpr_text)
    # Remove diretivas de compilação
    clean = remove_compiler_directives(clean)

    # Encontra o bloco uses ... ;
    match = re.search(r'\buses\b(.*?);', clean, re.IGNORECASE | re.DOTALL)
    if not match:
        return ''
    return match.group(1)


def parse_units(uses_section: str) -> list[dict]:
    """
    Analisa a seção uses e retorna lista de dicts:
      {
        'unit': 'NomeDaUnit',
        'path': 'caminho\\relativo\\arquivo.pas'  # None se sem caminho explícito
      }
    """
    units = []

    # Cada entrada pode ser:
    #   UnitName
    #   UnitName in 'path\to\unit.pas'
    #   UnitName in 'path\to\unit.pas' {FormClass}
    # Separadas por vírgula
    entries = re.split(r',', uses_section)

    pattern_with_path = re.compile(
        r"(\w+)\s+in\s+'([^']+)'",
        re.IGNORECASE
    )
    pattern_name_only = re.compile(r'\b(\w+)\b')

    for entry in entries:
        entry = entry.strip()
        if not entry:
            continue

        m = pattern_with_path.search(entry)
        if m:
            units.append({
                'unit': m.group(1),
                'path': m.group(2).replace('\\', '/'),
            })
        else:
            # Unit sem caminho (unit do sistema/VCL/RTL)
            m2 = pattern_name_only.search(entry)
            if m2:
                units.append({
                    'unit': m2.group(1),
                    'path': None,
                })

    return units


def find_sibling_files(source_file: Path) -> list[Path]:
    """
    Dado o caminho de um .pas, procura todos os arquivos com o mesmo
    nome base (qualquer extensão) no mesmo diretório.
    """
    found = [source_file] if source_file.exists() else []
    stem = source_file.stem
    directory = source_file.parent

    if directory.exists():
        for f in directory.iterdir():
            if f.stem.lower() == stem.lower() and f.suffix.lower() != source_file.suffix.lower():
                found.append(f)

    return found


_TEXT_EXTENSIONS = {'.pas', '.dfm', '.fmx', '.xfm', '.nfm'}


def validate_file_content(file_path: Path) -> tuple[bool, str]:
    """
    Valida o conteúdo de um arquivo antes de copiá-lo.
    Retorna (True, '') se válido, ou (False, razão) caso contrário.

    Apenas verifica extensões de texto (.pas, .dfm, .fmx, .xfm, .nfm).
    Extensões binárias (.dcu, .res, .resx etc.) são sempre consideradas válidas.

    Cheques realizados:
    - Ausência de null bytes (indica arquivo binário ou corrompido)
    - Decodificável como UTF-8 (fallback latin-1 para projetos Delphi antigos)
    """
    if file_path.suffix.lower() not in _TEXT_EXTENSIONS:
        return True, ''

    try:
        raw = file_path.read_bytes()
    except OSError as e:
        return False, f'read error: {e}'

    if b'\x00' in raw:
        return False, 'contains null bytes'

    try:
        raw.decode('utf-8')
    except UnicodeDecodeError:
        try:
            raw.decode('latin-1')
        except UnicodeDecodeError:
            return False, 'not decodable as UTF-8 or latin-1'

    return True, ''


def copy_unit_files(
    unit_info: dict,
    base_dir: Path,
    dest_dir: Path,
    preserve_structure: bool = True,
    max_file_size: int | None = None,
    validate_content: bool = False,
) -> tuple[list[Path], list[dict]]:
    """
    Copia os arquivos da unit para o destino.
    Retorna (lista de arquivos copiados, lista de arquivos ignorados).

    Cada entry em 'ignorados' é um dict com chaves:
      'file'   (Path)  — caminho do arquivo ignorado
      'unit'   (str)   — nome da unit
      'reason' (str)   — motivo do filtro

    Parâmetros:
      max_file_size   — tamanho máximo do .pas em KB; None = sem limite.
                        Se o .pas exceder o limite, toda a unit é ignorada.
      validate_content — se True, checar null bytes e encoding de cada arquivo
                         antes de copiar.
    """
    if unit_info['path'] is None:
        return [], []

    rel_path = Path(unit_info['path'])
    source_file = base_dir / rel_path

    siblings = find_sibling_files(source_file)
    if not siblings:
        return [], []

    copied: list[Path] = []
    skipped: list[dict] = []

    # Cheque de tamanho aplicado ao .pas — se falhar, ignora a unit inteira
    if max_file_size is not None and source_file.exists():
        size_kb = source_file.stat().st_size / 1024
        if size_kb > max_file_size:
            skipped.append({
                'file': source_file,
                'unit': unit_info['unit'],
                'reason': f'too large ({size_kb:.1f} KB > {max_file_size} KB limit)',
            })
            return copied, skipped

    for src in siblings:
        if validate_content:
            valid, reason = validate_file_content(src)
            if not valid:
                skipped.append({
                    'file': src,
                    'unit': unit_info['unit'],
                    'reason': f'content issue: {reason}',
                })
                continue

        if preserve_structure:
            # Mantém a estrutura de subdiretórios
            dest_file = dest_dir / src.relative_to(base_dir)
        else:
            # Tudo na raiz do destino
            dest_file = dest_dir / src.name

        dest_file.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest_file)
        copied.append(dest_file)

    return copied, skipped


def generate_codegraph_config(
    dest_dir: Path,
    skipped_files: list[dict],
    max_file_size_kb: int,
) -> Path:
    """
    Gera um arquivo .codegraph.json em dest_dir.

    Inclui excludes padrão para projetos Delphi e os stems dos arquivos
    que foram ignorados durante a cópia (para que o codegraph também os ignore).
    O maxFileSize é convertido para bytes, conforme esperado pelo codegraph.

    Retorna o Path do arquivo gerado.
    """
    standard_excludes = [
        '*.dcu',
        '*.bpl',
        '*.bpi',
        '*.dcp',
        '*.exe',
        '*.dll',
        '*.res',
        '*.rsm',
        '__history',
        '__recovery',
    ]

    skipped_excludes: list[str] = []
    for entry in skipped_files:
        pattern = f"{entry['file'].stem}.*"
        if pattern not in skipped_excludes and pattern not in standard_excludes:
            skipped_excludes.append(pattern)

    config = {
        'maxFileSize': max_file_size_kb * 1024,
        'exclude': standard_excludes + skipped_excludes,
    }

    config_path = dest_dir / '.codegraph.json'
    config_path.write_text(json.dumps(config, indent=2) + '\n', encoding='utf-8')
    return config_path


def parse_dpr(
    dpr_path: str,
    dest_dir: str,
    base_dir: str | None = None,
    flat: bool = False,
    max_file_size: int | None = None,
    validate_content: bool = False,
    codegraph_config: bool = False,
) -> None:
    dpr = Path(dpr_path).resolve()
    if not dpr.exists():
        print(f"Erro: arquivo '{dpr}' não encontrado.")
        sys.exit(1)

    # O diretório base padrão é o diretório do .dpr
    base = Path(base_dir).resolve() if base_dir else dpr.parent
    dest = Path(dest_dir).resolve()
    dest.mkdir(parents=True, exist_ok=True)

    print(f"Arquivo DPR : {dpr}")
    print(f"Diretório base : {base}")
    print(f"Destino     : {dest}")
    print()

    dpr_text = dpr.read_text(encoding='utf-8', errors='replace')
    uses_section = extract_uses_section(dpr_text)

    if not uses_section:
        print("Seção 'uses' não encontrada no arquivo .dpr.")
        sys.exit(1)

    units = parse_units(uses_section)
    print(f"Total de units encontradas : {len(units)}")

    units_with_path = [u for u in units if u['path']]
    units_system    = [u for u in units if not u['path']]

    print(f"  Com caminho explícito     : {len(units_with_path)}")
    print(f"  Sem caminho (sistema/VCL) : {len(units_system)}")
    print()

    total_copied = 0
    not_found = []
    all_skipped: list[dict] = []

    for u in units_with_path:
        copied, skipped = copy_unit_files(
            u, base, dest,
            preserve_structure=not flat,
            max_file_size=max_file_size,
            validate_content=validate_content,
        )
        all_skipped.extend(skipped)
        if copied:
            for f in copied:
                print(f"  [OK] {f.relative_to(dest)}")
            total_copied += len(copied)
        elif skipped:
            for s in skipped:
                print(f"  [IGNORADO] {u['unit']} -> {s['reason']}")
        else:
            rel = u['path']
            print(f"  [NÃO ENCONTRADO] {u['unit']} -> {rel}")
            not_found.append(u)

    print()
    print(f"Arquivos copiados : {total_copied}")
    if not_found:
        print(f"Não encontrados   : {len(not_found)}")
        for u in not_found:
            print(f"  - {u['unit']} ({u['path']})")

    if all_skipped:
        print(f"Ignorados (filtro): {len(all_skipped)}")
        for s in all_skipped:
            try:
                rel = s['file'].relative_to(dest)
            except ValueError:
                rel = s['file']
            print(f"  - {s['unit']} ({rel}): {s['reason']}")

    if units_system:
        print()
        print("Units de sistema/VCL (sem caminho — não copiadas):")
        names = ', '.join(u['unit'] for u in units_system)
        print(f"  {names}")

    if codegraph_config:
        effective_size = max_file_size if max_file_size is not None else 500
        cfg_path = generate_codegraph_config(dest, all_skipped, max_file_size_kb=effective_size)
        print(f"\nConfig gerado     : {cfg_path}")


def main():
    parser = argparse.ArgumentParser(
        description='Copia arquivos referenciados na seção uses de um .dpr Delphi.'
    )
    parser.add_argument('dpr_file', help='Caminho para o arquivo .dpr')
    parser.add_argument('dest_dir', help='Diretório de destino')
    parser.add_argument(
        '--base-dir',
        default=None,
        help='Diretório raiz do projeto (padrão: mesmo diretório do .dpr)',
    )
    parser.add_argument(
        '--flat',
        action='store_true',
        help='Copiar todos os arquivos direto na raiz do destino (sem manter estrutura)',
    )
    parser.add_argument(
        '--max-file-size',
        type=int,
        default=500,
        metavar='KB',
        help='Tamanho máximo do arquivo .pas em KB (padrão: 500). '
             'Arquivos maiores são ignorados para evitar erros WASM no codegraph.',
    )
    parser.add_argument(
        '--validate-content',
        action='store_true',
        help='Verificar conteúdo dos arquivos antes de copiar (null bytes, encoding).',
    )
    parser.add_argument(
        '--codegraph-config',
        action='store_true',
        help='Gerar .codegraph.json no diretório destino após copiar.',
    )
    args = parser.parse_args()
    parse_dpr(
        args.dpr_file,
        args.dest_dir,
        args.base_dir,
        args.flat,
        max_file_size=args.max_file_size,
        validate_content=args.validate_content,
        codegraph_config=args.codegraph_config,
    )


if __name__ == '__main__':
    main()
