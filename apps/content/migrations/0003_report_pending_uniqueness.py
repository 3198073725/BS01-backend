from django.db import migrations, models
from django.db.models import Q


def dedupe_pending_reports(apps, schema_editor):
    Report = apps.get_model('content', 'Report')

    seen = {}
    duplicate_ids = []
    for report in Report.objects.filter(status='pending').order_by('created_at', 'id').only(
        'id', 'reporter_id', 'target_type', 'target_id'
    ):
        key = (report.reporter_id, report.target_type, report.target_id)
        if key in seen:
            duplicate_ids.append(report.id)
        else:
            seen[key] = report.id

    if duplicate_ids:
        Report.objects.filter(id__in=duplicate_ids).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('content', '0002_alter_auditlog_options_alter_category_options_and_more'),
    ]

    operations = [
        migrations.RunPython(dedupe_pending_reports, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name='report',
            constraint=models.UniqueConstraint(
                fields=('reporter', 'target_type', 'target_id'),
                condition=Q(status='pending'),
                name='uniq_pending_report_per_target',
            ),
        ),
    ]
