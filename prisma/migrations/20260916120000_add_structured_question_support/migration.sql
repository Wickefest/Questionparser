-- Additive support for Paper 2/3 structured (multi-part, free-response)
-- questions alongside existing MCQ drafts/answers. Does not alter or drop
-- any existing MCQ data; answer_value/value are relaxed to nullable only
-- so a structured record can carry parts_answer instead, backed by a CHECK
-- constraint requiring at least one of the two to be present.

-- AlterEnum
ALTER TYPE "question_type" ADD VALUE 'STRUCTURED';

-- AlterTable: answer_records
ALTER TABLE "answer_records" ALTER COLUMN "answer_value" DROP NOT NULL;
ALTER TABLE "answer_records" ADD COLUMN "parts_answer" JSONB;
ALTER TABLE "answer_records" ADD CONSTRAINT "answer_records_value_or_parts_chk"
  CHECK ("answer_value" IS NOT NULL OR "parts_answer" IS NOT NULL);

-- AlterTable: question_answers
ALTER TABLE "question_answers" ALTER COLUMN "value" DROP NOT NULL;
ALTER TABLE "question_answers" ADD COLUMN "parts_answer" JSONB;
ALTER TABLE "question_answers" ADD CONSTRAINT "question_answers_value_or_parts_chk"
  CHECK ("value" IS NOT NULL OR "parts_answer" IS NOT NULL);
