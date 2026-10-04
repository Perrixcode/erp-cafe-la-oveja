"""Alta local interactiva: no recibe contraseñas por argumentos ni crea cuentas predeterminadas."""
import argparse
from getpass import getpass
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from erp.partner_auth import PartnerAuth, ROLE_LABELS
from erp.domain import DomainError


def main():
    parser = argparse.ArgumentParser(description='Crear una cuenta personal del ERP')
    parser.add_argument('--role', choices=ROLE_LABELS, default='partner')
    args = parser.parse_args()
    if not sys.stdin.isatty():
        raise SystemExit('Ejecuta este comando en tu Terminal; la contraseña se ingresa oculta.')
    try:
        print('Perfil: ' + ROLE_LABELS[args.role])
        username = input('Usuario personal: ').strip()
        name = input('Nombre: ').strip()
        password = getpass('Contraseña (mínimo 12 caracteres): ')
        if password != getpass('Repetir contraseña: '):
            raise DomainError('Las contraseñas no coinciden. No se creó la cuenta.')
        PartnerAuth(ROOT/'private/partner-auth.sqlite3').create(username, name, password, args.role)
        print('Cuenta creada. Ya puedes iniciar sesión en el ERP.')
    except (DomainError, EOFError, KeyboardInterrupt) as error:
        raise SystemExit(str(error) or 'Operación cancelada.')


if __name__ == '__main__':
    main()
