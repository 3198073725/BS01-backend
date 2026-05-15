from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('interactions', '0011_notification_actor_set_null'),
    ]

    operations = [
        migrations.AddConstraint(
            model_name='like',
            constraint=models.CheckConstraint(
                condition=(
                    (models.Q(comment__isnull=True, video__isnull=False))
                    | (models.Q(comment__isnull=False, video__isnull=True))
                ),
                name='chk_like_exactly_one_target',
            ),
        ),
    ]
