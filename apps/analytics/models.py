"""Analytics 模型定义模块。

用于存储埋点/统计类数据模型（如播放、停留、转化等指标）。
后续可在此定义模型类，并在 admin 中注册以便管理后台查看。
"""

import uuid
from django.db import models

class VideoStats(models.Model):
    """视频统计物化视图 - 对应 mv_video_stats 表（只读）"""
    video = models.OneToOneField('videos.Video', primary_key=True, on_delete=models.DO_NOTHING, db_column='video_id', related_name='stats', verbose_name="视频")
    view_count = models.BigIntegerField(verbose_name="播放次数")
    like_count = models.BigIntegerField(verbose_name="点赞数")
    comment_count = models.BigIntegerField(verbose_name="评论数")
    unique_likes = models.BigIntegerField(verbose_name="唯一点赞用户数")
    unique_comments = models.BigIntegerField(verbose_name="唯一评论用户数")
    avg_completion_rate = models.FloatField(verbose_name="平均完播率")

    class Meta:
        db_table = 'mv_video_stats'
        managed = False
        verbose_name = "视频统计"
        verbose_name_plural = "视频统计"
