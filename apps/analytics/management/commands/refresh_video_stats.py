from django.core.management.base import BaseCommand, CommandError
from django.db import connection

class Command(BaseCommand):
    help = 'Refresh materialized view mv_video_stats'

    def handle(self, *args, **options):
        prev = connection.get_autocommit()
        try:
            connection.set_autocommit(True)
            try:
                with connection.cursor() as cursor:
                    cursor.execute("REFRESH MATERIALIZED VIEW CONCURRENTLY mv_video_stats;")
                self.stdout.write(self.style.SUCCESS('Refreshed mv_video_stats concurrently'))
                return
            except Exception:
                with connection.cursor() as cursor:
                    cursor.execute("REFRESH MATERIALIZED VIEW mv_video_stats;")
                self.stdout.write(self.style.WARNING('Refreshed mv_video_stats without CONCURRENTLY'))
        except Exception as e:
            raise CommandError(str(e))
        finally:
            connection.set_autocommit(prev)
