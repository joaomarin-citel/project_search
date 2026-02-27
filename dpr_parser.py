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


def copy_unit_files(
    unit_info: dict,
    base_dir: Path,
    dest_dir: Path,
    preserve_structure: bool = True,
) -> list[Path]:
    """
    Copia os arquivos da unit para o destino.
    Retorna lista de arquivos copiados.
    """
    if unit_info['path'] is None:
        return []

    rel_path = Path(unit_info['path'])
    source_file = base_dir / rel_path

    siblings = find_sibling_files(source_file)
    if not siblings:
        return []

    copied = []
    for src in siblings:
        if preserve_structure:
            # Mantém a estrutura de subdiretórios
            dest_file = dest_dir / src.relative_to(base_dir)
        else:
            # Tudo na raiz do destino
            dest_file = dest_dir / src.name

        dest_file.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest_file)
        copied.append(dest_file)

    return copied


def parse_dpr(dpr_path: str, dest_dir: str, base_dir: str | None = None, flat: bool = False) -> None:
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

    for u in units_with_path:
        copied = copy_unit_files(u, base, dest, preserve_structure=not flat)
        if copied:
            for f in copied:
                print(f"  [OK] {f.relative_to(dest)}")
            total_copied += len(copied)
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

    if units_system:
        print()
        print("Units de sistema/VCL (sem caminho — não copiadas):")
        names = ', '.join(u['unit'] for u in units_system)
        print(f"  {names}")


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
    args = parser.parse_args()
    parse_dpr(args.dpr_file, args.dest_dir, args.base_dir, args.flat)


if __name__ == '__main__':
    main()
