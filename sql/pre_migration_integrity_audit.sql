-- Pre-migration integrity audit for recent interactions/videos constraints and related soft rules.
-- Read-only checks. Safe to run on staging or production before applying migrations.

-- Like: must point to exactly one target.
SELECT 'like_missing_target' AS check_code, COUNT(*) AS row_count
FROM interactions_like
WHERE video_id IS NULL AND comment_id IS NULL;

SELECT 'like_both_targets' AS check_code, COUNT(*) AS row_count
FROM interactions_like
WHERE video_id IS NOT NULL AND comment_id IS NOT NULL;

-- Video counters: must be non-negative.
SELECT 'video_negative_counters' AS check_code, COUNT(*) AS row_count
FROM videos_video
WHERE view_count < 0 OR like_count < 0 OR comment_count < 0;

-- Comment tree integrity.
SELECT 'comment_self_parent' AS check_code, COUNT(*) AS row_count
FROM interactions_comment
WHERE id = parent_id;

SELECT 'comment_cross_video_parent' AS check_code, COUNT(*) AS row_count
FROM interactions_comment c
JOIN interactions_comment p ON p.id = c.parent_id
WHERE c.video_id <> p.video_id;

SELECT 'comment_reply_to_second_level' AS check_code, COUNT(*) AS row_count
FROM interactions_comment c
JOIN interactions_comment p ON p.id = c.parent_id
WHERE p.parent_id IS NOT NULL;

-- Follow / History constraints added earlier.
SELECT 'follow_self_reference' AS check_code, COUNT(*) AS row_count
FROM interactions_follow
WHERE follower_id = followed_id;

SELECT 'history_invalid_progress' AS check_code, COUNT(*) AS row_count
FROM interactions_history
WHERE progress < 0 OR progress > 1;

SELECT 'history_negative_duration' AS check_code, COUNT(*) AS row_count
FROM interactions_history
WHERE watch_duration < 0;
