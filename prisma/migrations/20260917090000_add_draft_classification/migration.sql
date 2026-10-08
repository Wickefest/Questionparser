-- Additive: allow drafts (pre-approval) to carry a topic/subtopic classification
-- and Bloom's level, so the review queue can show classification before a
-- draft is promoted to an approved question_version. Nothing existing changes.

ALTER TABLE "question_drafts" ADD COLUMN "topic_node_id" UUID;
ALTER TABLE "question_drafts" ADD COLUMN "bloom_level" TEXT;
ALTER TABLE "question_drafts" ADD COLUMN "bloom_reasoning" TEXT;

ALTER TABLE "question_drafts"
  ADD CONSTRAINT "question_drafts_topic_node_id_fkey"
  FOREIGN KEY ("topic_node_id") REFERENCES "syllabus_nodes"("id") ON DELETE SET NULL;

CREATE INDEX "question_drafts_topic_node_id_idx" ON "question_drafts"("topic_node_id");
