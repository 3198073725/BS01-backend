import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('interactions', '0010_alter_like_unique_together_like_comment_and_more'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AlterField(
            model_name='notification',
            name='actor',
            field=models.ForeignKey(
                blank=True,
                null=True,
                db_column='actor_id',
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='activities',
                to=settings.AUTH_USER_MODEL,
                verbose_name='触发者',
            ),
        ),
    ]
