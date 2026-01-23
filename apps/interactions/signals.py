from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver
from django.db.models import F, Case, When, Value, IntegerField

from .models import Like, Follow, Comment, Favorite, Notification


@receiver(post_save, sender=Follow)
def increase_follow_counts(sender, instance: Follow, created: bool, **kwargs):
    if not created:
        return
    # 延迟导入避免循环依赖
    from apps.users.models import User
    User.objects.filter(id=instance.follower_id).update(following_count=F('following_count') + 1)
    User.objects.filter(id=instance.followed_id).update(followers_count=F('followers_count') + 1)
    try:
        if str(instance.follower_id) != str(instance.followed_id):
            Notification.objects.create(user_id=instance.followed_id, actor_id=instance.follower_id, verb='follow')
    except Exception:
        pass


@receiver(post_delete, sender=Follow)
def decrease_follow_counts(sender, instance: Follow, **kwargs):
    from apps.users.models import User
    # 防止计数出现负数
    User.objects.filter(id=instance.follower_id).update(
        following_count=Case(
            When(following_count__gt=0, then=F('following_count') - 1),
            default=Value(0),
            output_field=IntegerField(),
        )
    )
    User.objects.filter(id=instance.followed_id).update(
        followers_count=Case(
            When(followers_count__gt=0, then=F('followers_count') - 1),
            default=Value(0),
            output_field=IntegerField(),
        )
    )


@receiver(post_save, sender=Like)
def increase_video_like_count(sender, instance: Like, created: bool, **kwargs):
    if not created:
        return
    from apps.videos.models import Video
    Video.objects.filter(id=instance.video_id).update(like_count=F('like_count') + 1)
    try:
        v = Video.objects.only('id', 'user_id').filter(id=instance.video_id).first()
        if v and str(v.user_id) != str(instance.user_id):
            Notification.objects.create(user_id=v.user_id, actor_id=instance.user_id, verb='like', video_id=v.id)
    except Exception:
        pass


@receiver(post_delete, sender=Like)
def decrease_video_like_count(sender, instance: Like, **kwargs):
    from apps.videos.models import Video
    Video.objects.filter(id=instance.video_id).update(
        like_count=Case(
            When(like_count__gt=0, then=F('like_count') - 1),
            default=Value(0),
            output_field=IntegerField(),
        )
    )


@receiver(post_save, sender=Favorite)
def create_favorite_notification(sender, instance: Favorite, created: bool, **kwargs):
    if not created:
        return
    try:
        from apps.videos.models import Video
        v = Video.objects.only('id', 'user_id').filter(id=instance.video_id).first()
        if v and str(v.user_id) != str(instance.user_id):
            Notification.objects.create(user_id=v.user_id, actor_id=instance.user_id, verb='favorite', video_id=v.id)
    except Exception:
        pass

@receiver(post_save, sender=Comment)
def increase_video_comment_count(sender, instance: Comment, created: bool, **kwargs):
    if not created:
        return
    from apps.videos.models import Video
    Video.objects.filter(id=instance.video_id).update(comment_count=F('comment_count') + 1)
    try:
        if instance.parent_id:
            # 回复：通知被回复者
            parent = Comment.objects.only('id', 'user_id').filter(id=instance.parent_id).first()
            if parent and str(parent.user_id) != str(instance.user_id):
                Notification.objects.create(user_id=parent.user_id, actor_id=instance.user_id, verb='reply', video_id=instance.video_id, comment_id=instance.id)
        else:
            # 主评：通知视频作者
            from apps.videos.models import Video
            v = Video.objects.only('id', 'user_id').filter(id=instance.video_id).first()
            if v and str(v.user_id) != str(instance.user_id):
                Notification.objects.create(user_id=v.user_id, actor_id=instance.user_id, verb='comment', video_id=v.id, comment_id=instance.id)
    except Exception:
        pass

@receiver(post_delete, sender=Comment)
def decrease_video_comment_count(sender, instance: Comment, **kwargs):
    from apps.videos.models import Video
    Video.objects.filter(id=instance.video_id).update(
        comment_count=Case(
            When(comment_count__gt=0, then=F('comment_count') - 1),
            default=Value(0),
            output_field=IntegerField(),
        )
    )
