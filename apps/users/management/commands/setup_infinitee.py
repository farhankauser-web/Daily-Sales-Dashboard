"""
apps/users/management/commands/setup_infinitee.py

Run after first migrate:
  python manage.py setup_infinitee
"""
from django.core.management.base import BaseCommand
from django.conf import settings


class Command(BaseCommand):
    help = 'Seed default roles and create superuser for Infinitee Xclusives'

    def add_arguments(self, parser):
        parser.add_argument('--email',    default='admin@infiniteexclusives.com')
        parser.add_argument('--password', default='ChangeMe2024!')
        parser.add_argument('--name',     default='Farhan Kauser')

    def handle(self, *args, **opts):
        from apps.users.models import Role, User

        # Seed roles
        self.stdout.write('Creating default roles…')
        Role.create_defaults()
        self.stdout.write(self.style.SUCCESS(f'  ✓ {Role.objects.count()} roles ready'))

        # Create superuser if not exists
        email = opts['email']
        if not User.objects.filter(email=email).exists():
            names = opts['name'].split(None, 1)
            first = names[0]
            last  = names[1] if len(names) > 1 else ''
            u = User.objects.create_superuser(
                email=email,
                password=opts['password'],
                first_name=first,
                last_name=last,
            )
            self.stdout.write(self.style.SUCCESS(f'  ✓ Superuser created: {email}'))
        else:
            self.stdout.write(f'  – Superuser {email} already exists')

        # Create cache table notice
        self.stdout.write('\nNext step: run  python manage.py createcachetable')
        self.stdout.write(self.style.SUCCESS('\n✅ Setup complete! Visit http://127.0.0.1:8000/'))
