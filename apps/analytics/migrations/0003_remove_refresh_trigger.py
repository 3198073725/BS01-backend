from django.db import migrations

SQL_FORWARD = """
DROP TRIGGER IF EXISTS trg_refresh_stats ON videos_video;
DROP FUNCTION IF EXISTS refresh_video_stats();
"""

SQL_REVERSE = """
CREATE OR REPLACE FUNCTION refresh_video_stats()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
  REFRESH MATERIALIZED VIEW mv_video_stats;
  RETURN NULL;
END;
$$;

DROP TRIGGER IF EXISTS trg_refresh_stats ON videos_video;
CREATE TRIGGER trg_refresh_stats 
  AFTER INSERT OR UPDATE OR DELETE ON videos_video
  FOR EACH STATEMENT EXECUTE FUNCTION refresh_video_stats();
"""

class Migration(migrations.Migration):
    dependencies = [
        ('analytics', '0002_initial'),
    ]

    operations = [
        migrations.RunSQL(sql=SQL_FORWARD, reverse_sql=SQL_REVERSE),
    ]
