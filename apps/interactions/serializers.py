from rest_framework import serializers
from .models import Comment, History, Like, Favorite, Follow

class CommentSerializer(serializers.ModelSerializer):
    username = serializers.CharField(source='user.username', read_only=True)
    class Meta:
        model = Comment
        fields = ('id', 'user', 'username', 'video', 'parent', 'content', 'created_at')

class HistorySerializer(serializers.ModelSerializer):
    video_title = serializers.CharField(source='video.title', read_only=True)
    class Meta:
        model = History
        fields = ('id', 'user', 'video', 'video_title', 'created_at', 'progress')

class LikeSerializer(serializers.ModelSerializer):
    class Meta:
        model = Like
        fields = '__all__'

class FavoriteSerializer(serializers.ModelSerializer):
    class Meta:
        model = Favorite
        fields = '__all__'

class FollowSerializer(serializers.ModelSerializer):
    class Meta:
        model = Follow
        fields = '__all__'
