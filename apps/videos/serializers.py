from rest_framework import serializers
from apps.videos.models import Video
from apps.content.models import Category, Tag

class CategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = Category
        fields = ('id', 'name', 'description', 'created_at')

class TagSerializer(serializers.ModelSerializer):
    class Meta:
        model = Tag
        fields = ('id', 'name', 'created_at')

class VideoListSerializer(serializers.ModelSerializer):
    category_name = serializers.CharField(source='category.name', read_only=True)
    owner_username = serializers.CharField(source='user.username', read_only=True)
    owner_nickname = serializers.CharField(source='user.nickname', read_only=True)
    
    class Meta:
        model = Video
        fields = (
            'id', 'title', 'thumbnail', 'view_count', 'like_count', 
            'comment_count', 'duration', 'status', 'visibility', 
            'created_at', 'category_name', 'owner_username', 'owner_nickname'
        )

class VideoDetailSerializer(serializers.ModelSerializer):
    category = CategorySerializer(read_only=True)
    tags = serializers.SerializerMethodField()
    
    class Meta:
        model = Video
        fields = '__all__'

    def get_tags(self, obj: Video):
        try:
            vts = getattr(obj, 'video_tags', None)
            if vts is None:
                return []
            rows = list(vts.select_related('tag').all())
            tags = [getattr(r, 'tag', None) for r in rows]
            tags = [t for t in tags if t is not None]
            return TagSerializer(tags, many=True).data
        except Exception:
            return []
