"""
Rotate FIELD_ENCRYPTION_KEY — re-encrypt every EncryptedField value from the
CURRENT key (settings/.env) to a new one.

Why: if FIELD_ENCRYPTION_KEY was left at the insecure default, the code derives
a publicly-reproducible key, so all stored credentials are effectively readable
by anyone with the repo + a DB copy. This command migrates the ciphertext.

Run it with the web service STOPPED and a fresh DB backup:

    1) new=$(./venv/bin/python -c "from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())")
    2) ./venv/bin/python manage.py rotate_field_encryption_key --new-key "$new"
    3) put  FIELD_ENCRYPTION_KEY=$new  in .env   →   start the service
    4) verify a credential still decrypts (e.g. the SP-API test)

The command reads with the current key, swaps in-process, re-encrypts, and
verifies every field round-trips before committing. It does NOT touch .env —
you set the new key there yourself in step 3.
"""
from django.core.management.base import BaseCommand, CommandError
from django.conf import settings
from django.db import transaction
from django.apps import apps as django_apps
from cryptography.fernet import Fernet

from apps.amazon_api.models import EncryptedField


class Command(BaseCommand):
    help = 'Re-encrypt all EncryptedField data under a new FIELD_ENCRYPTION_KEY.'

    def add_arguments(self, parser):
        parser.add_argument('--new-key', required=True,
                            help='New Fernet key (44-char urlsafe base64).')
        parser.add_argument('--dry-run', action='store_true',
                            help='Read + count only; write nothing.')

    def handle(self, *, new_key, dry_run, **_):
        try:
            Fernet(new_key.encode())
        except Exception as e:
            raise CommandError(f'--new-key is not a valid Fernet key: {e}')
        if new_key == settings.FIELD_ENCRYPTION_KEY:
            raise CommandError('--new-key is identical to the current key.')

        # Discover every EncryptedField on every model.
        targets = []
        for model in django_apps.get_models():
            fields = [f.name for f in model._meta.get_fields()
                      if isinstance(f, EncryptedField)]
            if fields:
                targets.append((model, fields))

        # Phase 1 — decrypt everything with the CURRENT key.
        buf = []
        for model, fields in targets:
            rows = list(model.objects.all())
            for obj in rows:
                buf.append((model, obj.pk, {f: getattr(obj, f) for f in fields}))
            self.stdout.write(f'  {model.__name__:22} rows={len(rows):3}  fields={fields}')
        self.stdout.write(self.style.MIGRATE_HEADING(
            f'Read {len(buf)} record(s) with the current key.'))

        if dry_run:
            self.stdout.write('DRY RUN — nothing written.')
            return

        # Phase 2 — swap key in-process, re-encrypt, verify, commit atomically.
        settings.FIELD_ENCRYPTION_KEY = new_key
        with transaction.atomic():
            for model, pk, vals in buf:
                obj = model.objects.get(pk=pk)
                for f, v in vals.items():
                    setattr(obj, f, v)
                obj.save(update_fields=list(vals.keys()))

            bad = 0
            for model, pk, vals in buf:
                obj = model.objects.get(pk=pk)
                for f, v in vals.items():
                    if getattr(obj, f) != v:
                        bad += 1
                        self.stderr.write(f'  MISMATCH {model.__name__}#{pk}.{f}')
            if bad:
                raise CommandError(
                    f'{bad} field(s) failed round-trip verification — rolled back.')

        self.stdout.write(self.style.SUCCESS(
            f'✅ Re-encrypted + verified {len(buf)} record(s) with the new key.'))
        self.stdout.write(self.style.WARNING(
            'NEXT: set FIELD_ENCRYPTION_KEY=<new-key> in .env, then restart the service.'))
