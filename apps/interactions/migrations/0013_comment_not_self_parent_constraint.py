from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('interactions', '0012_like_exactly_one_target_constraint'),
    ]

    operations = [
        migrations.AddConstraint(
            model_name='comment',
            constraint=models.CheckConstraint(
                condition=~models.Q(parent=models.F('id')),
                name='chk_comment_not_self_parent',
            ),
        ),
    ]
