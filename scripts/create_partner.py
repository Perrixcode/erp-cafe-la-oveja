"""Alta de un socio desde el equipo local, con contraseña oculta y sin red."""
from getpass import getpass
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from erp.partner_auth import PartnerAuth
from erp.domain import DomainError

if __name__ == '__main__':
    if not sys.stdin.isatty():
        raise SystemExit('Ejecuta este comando en tu Terminal; la contraseña se ingresa oculta.')
    try:
        username = input('Usuario del socio: ').strip()
        name = input('Nombre para el historial: ').strip()
        password = getpass('Contraseña (mínimo 12 caracteres): ')
        if password != getpass('Repetir contraseña: '):
            raise DomainError('Las contraseñas no coinciden. No se creó la cuenta.')
        PartnerAuth(ROOT/'private/partner-auth.sqlite3').create(username,name,password)
        print('Socio creado. Ya puedes iniciar sesión en la pantalla de acceso del ERP.')
    except (DomainError,EOFError,KeyboardInterrupt) as error:
        raise SystemExit(str(error) or 'Operación cancelada.')
