"""Validate bounded, non-macro OOXML workbooks without extracting or evaluating them."""
from io import BytesIO
import posixpath
import stat
from urllib.parse import unquote, urlsplit
import xml.etree.ElementTree as ET
import zipfile
import zlib

from erp.domain import DomainError

MIME = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
MAX_EXPANDED = 32 * 1024 * 1024
MAX_PART = 8 * 1024 * 1024
MAX_ENTRIES = 1024
CT = 'http://schemas.openxmlformats.org/package/2006/content-types'
REL = 'http://schemas.openxmlformats.org/package/2006/relationships'
DOCREL = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
SHEET = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
WORKBOOK_TYPE = MIME + '.main+xml'


def invalid(message='XLSX dañado o no válido. Usa un libro Excel estándar sin macros.'):
    raise DomainError(message, 415)


def xml(content):
    # OOXML XML is UTF-8. Reject DTD/entity declarations before any XML parsing.
    try:
        text = content.decode('utf-8-sig')
    except UnicodeError:
        invalid()
    if '\x00' in text or '<!doctype' in text.casefold() or '<!entity' in text.casefold():
        invalid('El XLSX contiene declaraciones XML no permitidas.')
    try:
        return ET.fromstring(text)
    except ET.ParseError:
        invalid()


def validate(content):
    try:
        with zipfile.ZipFile(BytesIO(content)) as archive:
            entries = archive.infolist()
            if not entries or len(entries) > MAX_ENTRIES or sum(i.file_size for i in entries) > MAX_EXPANDED:
                raise DomainError('XLSX demasiado grande al descomprimir: máximo 32 MB y 1024 partes.', 413)
            parts = {}; seen = set()
            for item in entries:
                name = item.filename
                key = name.casefold()
                if key in seen or not name or name.startswith('/') or '\\' in name or any(p in ('', '.', '..') for p in name.rstrip('/').split('/')):
                    invalid()
                seen.add(key)
                if item.flag_bits & 1 or stat.S_ISLNK(item.external_attr >> 16) or item.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
                    invalid()
                if item.file_size > MAX_PART:
                    raise DomainError('Una parte del XLSX supera el límite de 8 MB.', 413)
                if any(word in key for word in ('vbaproject', 'macrosheet', 'activex', 'embeddings/', 'externallinks/', 'customui/')):
                    invalid('No se admiten macros, objetos incrustados ni conexiones externas en XLSX.')
                if not item.is_dir():
                    parts[name] = archive.read(item)
            required = {'[Content_Types].xml', '_rels/.rels', 'xl/workbook.xml', 'xl/_rels/workbook.xml.rels'}
            if not required <= parts.keys():
                invalid()
            types = xml(parts['[Content_Types].xml'])
            if types.tag != '{' + CT + '}Types':
                invalid()
            overrides = {}; defaults = {}
            for entry in types:
                value = entry.get('ContentType', '')
                if any(word in value.casefold() for word in ('macro', 'vba', 'activex', 'oleobject')):
                    invalid('No se admiten macros ni contenido activo en XLSX.')
                if entry.tag == '{' + CT + '}Override':
                    name = entry.get('PartName', '')
                    if not name.startswith('/') or name[1:] not in parts or name[1:] in overrides:
                        invalid()
                    overrides[name[1:]] = value
                elif entry.tag == '{' + CT + '}Default':
                    extension = entry.get('Extension', '')
                    if not extension or extension in defaults:
                        invalid()
                    defaults[extension] = value
                else:
                    invalid()
            if overrides.get('xl/workbook.xml', defaults.get('xml')) != WORKBOOK_TYPE:
                invalid()
            relationships = {}; root_tags = {}
            for name, data in parts.items():
                if name == '[Content_Types].xml':
                    continue
                content_type = overrides.get(name, defaults.get(name.rsplit('.', 1)[-1], ''))
                if not content_type:
                    invalid()
                if name.endswith(('.xml', '.rels')) or content_type.endswith('+xml') or content_type in ('application/xml', 'text/xml'):
                    root = xml(data)
                    root_tags[name] = root.tag
                    if name.endswith('.rels'):
                        if root.tag != '{' + REL + '}Relationships':
                            invalid()
                        if name == '_rels/.rels':
                            owner = ''
                        elif '/_rels/' in name:
                            prefix, leaf = name.rsplit('/_rels/', 1)
                            owner = prefix + '/' + leaf[:-5]
                            if owner not in parts:
                                invalid()
                        else:
                            invalid()
                        links = {}
                        for link in root:
                            target = link.get('Target', '')
                            rid = link.get('Id', '')
                            kind = link.get('Type', '')
                            if link.tag != '{' + REL + '}Relationship' or not rid or rid in links or link.get('TargetMode', 'Internal') != 'Internal':
                                invalid('El XLSX contiene relaciones externas o no válidas.')
                            if not target or '\\' in target or urlsplit(target).scheme or urlsplit(target).netloc or urlsplit(target).query or urlsplit(target).fragment:
                                invalid()
                            resolved = posixpath.normpath(unquote(target).lstrip('/') if target.startswith('/') else posixpath.join(posixpath.dirname(owner), unquote(target)))
                            if resolved.startswith('../') or resolved not in parts or any(word in kind.casefold() for word in ('vba', 'macro', 'activex', 'oleobject', 'externallink')):
                                invalid()
                            links[rid] = (kind, resolved)
                        relationships[owner] = links
            root_links = relationships.get('', {}).values()
            if sum(kind == DOCREL + '/officeDocument' and path == 'xl/workbook.xml' for kind, path in root_links) != 1:
                invalid()
            book = xml(parts['xl/workbook.xml'])
            sheets = book.find('{' + SHEET + '}sheets')
            if book.tag != '{' + SHEET + '}workbook' or sheets is None or not 1 <= len(sheets) <= 256:
                invalid()
            names = set(); targets = set()
            for sheet in sheets:
                name = sheet.get('name', '')
                link = relationships.get('xl/workbook.xml', {}).get(sheet.get('{' + DOCREL + '}id'))
                if sheet.tag != '{' + SHEET + '}sheet' or not name or name.casefold() in names or not link or link[0] != DOCREL + '/worksheet' or root_tags.get(link[1]) != '{' + SHEET + '}worksheet' or link[1] in targets:
                    invalid()
                names.add(name.casefold()); targets.add(link[1])
    except (zipfile.BadZipFile, RuntimeError, ValueError, KeyError, NotImplementedError, zlib.error):
        invalid()
    return MIME
