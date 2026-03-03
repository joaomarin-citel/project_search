#!/usr/bin/env python3
"""Testes unitários para dpr_parser.py"""

import unittest
import tempfile
import shutil
from pathlib import Path

from dpr_parser import (
    strip_block_comments,
    remove_compiler_directives,
    extract_uses_section,
    parse_units,
    find_sibling_files,
    copy_unit_files,
    validate_file_content,
    generate_codegraph_config,
)

# Trecho realista do autcom.dpr
SAMPLE_DPR = r"""
program autcom;
{$R *.dres}
uses
  ShareMem,
  madExcept,
  madLinkDisAsm,
  madListHardware,
  madListProcesses,
  madListModules,
  Forms,
  SysUtils,
  Windows,
  {$IFNDEF WIN64}
  RlConsts,
  {$ENDIF }
  Vcl.Themes,
  {$IFDEF TIMERLOG}
  untTimerLog in 'fontes\genericos\untTimerLog.pas',
  {$ENDIF }
  VirtualUI_AutoRun in 'VirtualUI\VirtualUI_AutoRun.pas',
  VirtualUI_SDK in 'VirtualUI\VirtualUI_SDK.pas',
  untPai in 'fontes\genericos\untPai.pas' {frmPai},
  untPaiTransfCod in 'fontes\genericos\untPaiTransfCod.pas' {frmPaiTransfCod},
  untPaiCad in 'fontes\genericos\untPaiCad.pas' {frmPaiCad},
  {$IFNDEF WIN64}
  untPaiQck in 'fontes\genericos\untPaiQck.pas' {frmPaiQck},
  {$ENDIF }
  untPaiRelatorio in 'fontes\genericos\untPaiRelatorio.pas' {frmPaiRelatorio},
  untPaiSimples in 'fontes\genericos\untPaiSimples.pas' {frmPaiSimples},
  untHighlightGrid in 'fontes\genericos\untHighlightGrid.pas',
  untTypesPainel in 'fontes\genericos\untTypesPainel.pas',
  untAtributosProduto in 'fontes\cadastros\untAtributosProduto.pas',
  untCadGrupoAtributo in 'fontes\cadastros\untCadGrupoAtributo.pas' {frmCadGrupoAtributo},
  untCadAtributos in 'fontes\cadastros\untCadAtributos.pas' {frmCadAtributos},
  untCadAtividades in 'fontes\Cadastros\untCadAtividades.pas' {frmCadAtividades},
  untCadCartoes in 'fontes\Cadastros\untCadCartoes.pas' {frmCadCartoes};

begin
  Application.Initialize;
  Application.Run;
end.
"""


class TestStripComments(unittest.TestCase):
    def test_removes_block_comment(self):
        text = "foo { comentario } bar"
        self.assertEqual(strip_block_comments(text).strip(), "foo  bar".strip())

    def test_keeps_compiler_directive(self):
        text = "foo {$IFDEF WIN64} bar"
        result = strip_block_comments(text)
        self.assertIn('{$IFDEF WIN64}', result)

    def test_removes_line_comment(self):
        text = "foo // comentario\nbar"
        result = strip_block_comments(text)
        self.assertIn('foo ', result)
        self.assertIn('bar', result)
        self.assertNotIn('comentario', result)

    def test_form_reference_removed(self):
        # {frmPai} é um comentário de bloco comum — deve ser removido
        text = "untPai in 'fontes/untPai.pas' {frmPai}"
        result = strip_block_comments(text)
        self.assertNotIn('frmPai', result)
        self.assertIn("untPai in 'fontes/untPai.pas'", result)


class TestRemoveDirectives(unittest.TestCase):
    def test_removes_ifdef(self):
        text = "{$IFDEF WIN64} foo {$ENDIF}"
        result = remove_compiler_directives(text)
        self.assertNotIn('{$', result)
        self.assertIn('foo', result)


class TestExtractUsesSection(unittest.TestCase):
    def test_finds_uses(self):
        section = extract_uses_section(SAMPLE_DPR)
        self.assertTrue(len(section) > 0)

    def test_contains_unit_names(self):
        section = extract_uses_section(SAMPLE_DPR)
        self.assertIn('ShareMem', section)
        self.assertIn('VirtualUI_AutoRun', section)
        self.assertIn('untPai', section)

    def test_no_uses_returns_empty(self):
        result = extract_uses_section("program foo; begin end.")
        self.assertEqual(result, '')


class TestParseUnits(unittest.TestCase):
    def setUp(self):
        self.section = extract_uses_section(SAMPLE_DPR)
        self.units = parse_units(self.section)
        self.by_name = {u['unit']: u for u in self.units}

    def test_system_units_have_no_path(self):
        self.assertIsNone(self.by_name['ShareMem']['path'])
        self.assertIsNone(self.by_name['Forms']['path'])
        self.assertIsNone(self.by_name['SysUtils']['path'])

    def test_units_with_path(self):
        self.assertEqual(
            self.by_name['VirtualUI_AutoRun']['path'],
            'VirtualUI/VirtualUI_AutoRun.pas',
        )
        self.assertEqual(
            self.by_name['untPai']['path'],
            'fontes/genericos/untPai.pas',
        )

    def test_conditional_units_included(self):
        # units dentro de {$IFDEF} / {$IFNDEF} devem ser incluídas
        self.assertIn('untTimerLog', self.by_name)
        self.assertIn('untPaiQck', self.by_name)

    def test_total_count(self):
        # Deve ter pelo menos as units explicitamente listadas
        self.assertGreaterEqual(len(self.units), 20)


class TestFindSiblingFiles(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        # Cria arquivos de teste
        (self.tmp / 'untPai.pas').write_text('unit untPai;')
        (self.tmp / 'untPai.dfm').write_text('object frmPai: TfrmPai end')
        (self.tmp / 'untPai.fmx').write_text('object frmPai: TfrmPai end')
        (self.tmp / 'outro.pas').write_text('unit outro;')

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_finds_all_siblings(self):
        source = self.tmp / 'untPai.pas'
        siblings = find_sibling_files(source)
        names = {f.name for f in siblings}
        self.assertIn('untPai.pas', names)
        self.assertIn('untPai.dfm', names)
        self.assertIn('untPai.fmx', names)
        self.assertNotIn('outro.pas', names)

    def test_nonexistent_pas_still_finds_siblings(self):
        # O .pas pode não existir mas o .dfm sim
        source = self.tmp / 'untPai.pas'
        siblings = find_sibling_files(source)
        self.assertTrue(len(siblings) >= 1)


class TestCopyUnitFiles(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.base = self.tmp / 'projeto'
        self.dest = self.tmp / 'destino'

        # Estrutura de arquivos fictícia
        fontes = self.base / 'fontes' / 'genericos'
        fontes.mkdir(parents=True)
        (fontes / 'untPai.pas').write_text('unit untPai;')
        (fontes / 'untPai.dfm').write_text('object frmPai end')

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_copies_pas_and_dfm(self):
        unit_info = {'unit': 'untPai', 'path': 'fontes/genericos/untPai.pas'}
        primary, large, skipped = copy_unit_files(unit_info, self.base, self.dest, preserve_structure=True)
        names = {f.name for f in primary}
        self.assertIn('untPai.pas', names)
        self.assertIn('untPai.dfm', names)
        self.assertEqual(large, [])
        self.assertEqual(skipped, [])

    def test_preserves_directory_structure(self):
        unit_info = {'unit': 'untPai', 'path': 'fontes/genericos/untPai.pas'}
        copy_unit_files(unit_info, self.base, self.dest, preserve_structure=True)
        expected = self.dest / 'fontes' / 'genericos' / 'untPai.pas'
        self.assertTrue(expected.exists())

    def test_flat_mode(self):
        unit_info = {'unit': 'untPai', 'path': 'fontes/genericos/untPai.pas'}
        copy_unit_files(unit_info, self.base, self.dest, preserve_structure=False)
        expected = self.dest / 'untPai.pas'
        self.assertTrue(expected.exists())

    def test_no_path_returns_empty(self):
        unit_info = {'unit': 'Forms', 'path': None}
        primary, large, skipped = copy_unit_files(unit_info, self.base, self.dest)
        self.assertEqual(primary, [])
        self.assertEqual(large, [])
        self.assertEqual(skipped, [])

    def test_missing_file_returns_empty(self):
        unit_info = {'unit': 'untNaoExiste', 'path': 'fontes/untNaoExiste.pas'}
        primary, large, skipped = copy_unit_files(unit_info, self.base, self.dest)
        self.assertEqual(primary, [])
        self.assertEqual(large, [])
        self.assertEqual(skipped, [])

    def test_file_within_size_limit_is_copied(self):
        unit_info = {'unit': 'untPai', 'path': 'fontes/genericos/untPai.pas'}
        primary, large, skipped = copy_unit_files(
            unit_info, self.base, self.dest,
            preserve_structure=True,
            max_file_size=500,
        )
        self.assertIn('untPai.pas', {f.name for f in primary})
        self.assertEqual(large, [])
        self.assertEqual(skipped, [])

    def test_file_exceeding_size_limit_is_skipped_without_large_dir(self):
        large_pas = self.base / 'fontes' / 'genericos' / 'untPai.pas'
        large_pas.write_bytes(b'x' * 1025)  # 1025 bytes > 1 KB limit
        unit_info = {'unit': 'untPai', 'path': 'fontes/genericos/untPai.pas'}
        primary, large, skipped = copy_unit_files(
            unit_info, self.base, self.dest,
            preserve_structure=True,
            max_file_size=1,
        )
        self.assertEqual(primary, [])
        self.assertEqual(large, [])
        self.assertEqual(len(skipped), 1)
        self.assertIn('too large', skipped[0]['reason'])
        self.assertEqual(skipped[0]['unit'], 'untPai')

    def test_file_exceeding_size_limit_routed_to_large_dir(self):
        large_pas = self.base / 'fontes' / 'genericos' / 'untPai.pas'
        large_pas.write_bytes(b'x' * 1025)
        large_dest = self.tmp / 'large'
        large_dest.mkdir()
        unit_info = {'unit': 'untPai', 'path': 'fontes/genericos/untPai.pas'}
        primary, large, skipped = copy_unit_files(
            unit_info, self.base, self.dest,
            preserve_structure=True,
            max_file_size=1,
            large_dir=large_dest,
        )
        self.assertEqual(primary, [])
        self.assertEqual(skipped, [])
        self.assertGreater(len(large), 0)
        # Deve manter a estrutura de subdiretórios no large_dir
        expected = large_dest / 'fontes' / 'genericos' / 'untPai.pas'
        self.assertTrue(expected.exists())

    def test_large_dir_copies_siblings_too(self):
        large_pas = self.base / 'fontes' / 'genericos' / 'untPai.pas'
        large_pas.write_bytes(b'x' * 1025)
        large_dest = self.tmp / 'large'
        large_dest.mkdir()
        unit_info = {'unit': 'untPai', 'path': 'fontes/genericos/untPai.pas'}
        primary, large, skipped = copy_unit_files(
            unit_info, self.base, self.dest,
            preserve_structure=True,
            max_file_size=1,
            large_dir=large_dest,
        )
        names = {f.name for f in large}
        self.assertIn('untPai.pas', names)
        self.assertIn('untPai.dfm', names)

    def test_no_size_limit_copies_everything(self):
        unit_info = {'unit': 'untPai', 'path': 'fontes/genericos/untPai.pas'}
        primary, large, skipped = copy_unit_files(
            unit_info, self.base, self.dest,
            preserve_structure=True,
            max_file_size=None,
        )
        self.assertGreater(len(primary), 0)
        self.assertEqual(large, [])
        self.assertEqual(skipped, [])


class TestValidateFileContent(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_valid_utf8_file(self):
        f = self.tmp / 'good.pas'
        f.write_text('unit GoodUnit;\nbegin end.', encoding='utf-8')
        valid, reason = validate_file_content(f)
        self.assertTrue(valid)
        self.assertEqual(reason, '')

    def test_valid_latin1_file(self):
        # latin-1 com acentos — comum em projetos Delphi antigos
        f = self.tmp / 'latin1.pas'
        f.write_bytes('unit OldUnit; // coment\xe1rio'.encode('latin-1'))
        valid, reason = validate_file_content(f)
        self.assertTrue(valid)

    def test_null_bytes_rejected(self):
        f = self.tmp / 'binary.pas'
        f.write_bytes(b'unit Bad;\x00\x00garbage')
        valid, reason = validate_file_content(f)
        self.assertFalse(valid)
        self.assertIn('null bytes', reason)

    def test_binary_extension_not_checked(self):
        # .dcu é extensão binária — deve ser sempre considerado válido
        f = self.tmp / 'compiled.dcu'
        f.write_bytes(b'\x00\x01\x02\x03compiled dcu binary')
        valid, reason = validate_file_content(f)
        self.assertTrue(valid)

    def test_dfm_with_null_bytes_rejected(self):
        f = self.tmp / 'form.dfm'
        f.write_bytes(b'object Form1: TForm1\x00end')
        valid, reason = validate_file_content(f)
        self.assertFalse(valid)
        self.assertIn('null bytes', reason)


class TestGenerateCodegraphConfig(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def _load_config(self):
        import json
        return json.loads((self.tmp / '.codegraph.json').read_text())

    def test_creates_config_file(self):
        generate_codegraph_config(self.tmp, [], max_file_size_kb=500)
        self.assertTrue((self.tmp / '.codegraph.json').exists())

    def test_config_contains_max_file_size_in_bytes(self):
        generate_codegraph_config(self.tmp, [], max_file_size_kb=500)
        data = self._load_config()
        self.assertEqual(data['maxFileSize'], 500 * 1024)

    def test_config_contains_standard_excludes(self):
        generate_codegraph_config(self.tmp, [], max_file_size_kb=500)
        data = self._load_config()
        self.assertIn('*.dcu', data['exclude'])
        self.assertIn('*.exe', data['exclude'])
        self.assertIn('__history', data['exclude'])

    def test_skipped_files_added_to_excludes(self):
        fake_path = self.tmp / 'fontes' / 'untLarge.pas'
        skipped = [{'file': fake_path, 'unit': 'untLarge', 'reason': 'too large'}]
        generate_codegraph_config(self.tmp, skipped, max_file_size_kb=500)
        data = self._load_config()
        self.assertIn('untLarge.*', data['exclude'])

    def test_no_duplicate_excludes(self):
        # Mesmo stem em .pas e .dfm — deve aparecer apenas uma vez no exclude
        fake_pas = self.tmp / 'untDup.pas'
        fake_dfm = self.tmp / 'untDup.dfm'
        skipped = [
            {'file': fake_pas, 'unit': 'untDup', 'reason': 'too large'},
            {'file': fake_dfm, 'unit': 'untDup', 'reason': 'content issue: null bytes'},
        ]
        generate_codegraph_config(self.tmp, skipped, max_file_size_kb=500)
        data = self._load_config()
        self.assertEqual(data['exclude'].count('untDup.*'), 1)


if __name__ == '__main__':
    unittest.main(verbosity=2)
