-- Additive Question Factory + trusted Question Bank + Syllabus schema.
-- Does not drop or alter legacy ingest tables: question_banks,
-- questions, question_options, question_media.
-- Trusted-bank rows live in "bank_questions" so the legacy "questions"
-- table (supabase_store.push_canonical_bank) can remain untouched.

-- CreateEnum
CREATE TYPE "syllabus_node_type" AS ENUM ('SECTION', 'TOPIC', 'SUBTOPIC', 'OUTCOME');

-- CreateEnum
CREATE TYPE "ingestion_batch_status" AS ENUM ('UPLOADED', 'PROCESSING', 'READY_FOR_REVIEW', 'HAS_ERRORS', 'COMPLETED');

-- CreateEnum
CREATE TYPE "source_document_type" AS ENUM ('QUESTION_PAPER', 'ANSWER_SCHEME');

-- CreateEnum
CREATE TYPE "ingestion_run_status" AS ENUM ('PENDING', 'PROCESSING', 'COMPLETED', 'FAILED');

-- CreateEnum
CREATE TYPE "question_type" AS ENUM ('MCQ');

-- CreateEnum
CREATE TYPE "draft_status" AS ENUM ('DRAFT', 'NEEDS_REVIEW', 'READY', 'REJECTED', 'APPROVED');

-- CreateEnum
CREATE TYPE "explanation_source" AS ENUM ('MARK_SCHEME', 'AI');

-- CreateEnum
CREATE TYPE "asset_role" AS ENUM ('STEM_DIAGRAM', 'STEM_GRAPH', 'STEM_IMAGE', 'COMPOSITE_OPTIONS', 'TABLE_FALLBACK', 'SOURCE_CROP');

-- CreateEnum
CREATE TYPE "review_action" AS ENUM ('EDIT', 'APPROVE', 'REJECT', 'REQUEST_REPARSE');

-- CreateEnum
CREATE TYPE "question_version_status" AS ENUM ('APPROVED', 'PUBLISHED', 'RETIRED');

-- CreateEnum
CREATE TYPE "classification_status" AS ENUM ('PENDING', 'CLASSIFIED', 'NEEDS_REVIEW', 'APPROVED');

-- CreateTable
CREATE TABLE "syllabuses" (
    "id" UUID NOT NULL DEFAULT gen_random_uuid(),
    "subject" TEXT NOT NULL,
    "code" TEXT NOT NULL,
    "year" INTEGER NOT NULL,
    "title" TEXT NOT NULL,
    "created_at" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updated_at" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "syllabuses_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "syllabus_nodes" (
    "id" UUID NOT NULL DEFAULT gen_random_uuid(),
    "syllabus_id" UUID NOT NULL,
    "parent_id" UUID,
    "code" TEXT,
    "title" TEXT NOT NULL,
    "outcomes_text" TEXT,
    "node_type" "syllabus_node_type" NOT NULL,
    "sort_order" INTEGER,
    "created_at" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updated_at" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "syllabus_nodes_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "ingestion_batches" (
    "id" UUID NOT NULL DEFAULT gen_random_uuid(),
    "title" TEXT NOT NULL,
    "subject" TEXT NOT NULL,
    "exam_year" INTEGER,
    "paper_code" TEXT,
    "syllabus_id" UUID,
    "uploaded_by" UUID,
    "status" "ingestion_batch_status" NOT NULL,
    "created_at" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updated_at" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "ingestion_batches_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "source_documents" (
    "id" UUID NOT NULL DEFAULT gen_random_uuid(),
    "batch_id" UUID NOT NULL,
    "document_type" "source_document_type" NOT NULL,
    "storage_path" TEXT NOT NULL,
    "original_name" TEXT NOT NULL,
    "mime_type" TEXT NOT NULL,
    "page_count" INTEGER,
    "sha256" TEXT,
    "created_at" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "source_documents_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "ingestion_runs" (
    "id" UUID NOT NULL DEFAULT gen_random_uuid(),
    "batch_id" UUID NOT NULL,
    "parser_version" TEXT,
    "model_name" TEXT,
    "status" "ingestion_run_status" NOT NULL,
    "started_at" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "completed_at" TIMESTAMPTZ,

    CONSTRAINT "ingestion_runs_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "question_drafts" (
    "id" UUID NOT NULL DEFAULT gen_random_uuid(),
    "run_id" UUID NOT NULL,
    "batch_id" UUID NOT NULL,
    "question_ref" TEXT NOT NULL,
    "question_type" "question_type" NOT NULL,
    "content" JSONB NOT NULL,
    "options" JSONB NOT NULL,
    "source_page" INTEGER,
    "source_regions" JSONB,
    "status" "draft_status" NOT NULL,
    "requires_review" BOOLEAN NOT NULL DEFAULT false,
    "validation_issues" JSONB,
    "created_at" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updated_at" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "question_drafts_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "answer_records" (
    "id" UUID NOT NULL DEFAULT gen_random_uuid(),
    "run_id" UUID NOT NULL,
    "batch_id" UUID NOT NULL,
    "question_ref" TEXT NOT NULL,
    "answer_value" TEXT NOT NULL,
    "explanation" TEXT,
    "explanation_source" "explanation_source",
    "source_page" INTEGER,
    "created_at" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "answer_records_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "question_assets" (
    "id" UUID NOT NULL DEFAULT gen_random_uuid(),
    "draft_id" UUID,
    "source_document_id" UUID,
    "role" "asset_role" NOT NULL,
    "storage_path" TEXT NOT NULL,
    "mime_type" TEXT NOT NULL,
    "source_page" INTEGER,
    "bbox" JSONB,
    "width" INTEGER,
    "height" INTEGER,
    "sha256" TEXT,
    "created_at" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "question_assets_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "review_events" (
    "id" UUID NOT NULL DEFAULT gen_random_uuid(),
    "draft_id" UUID NOT NULL,
    "reviewer_id" UUID,
    "action" "review_action" NOT NULL,
    "comment" TEXT,
    "created_at" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "review_events_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "bank_questions" (
    "id" UUID NOT NULL DEFAULT gen_random_uuid(),
    "source_key" TEXT NOT NULL,
    "subject" TEXT NOT NULL,
    "created_at" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updated_at" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "bank_questions_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "question_versions" (
    "id" UUID NOT NULL DEFAULT gen_random_uuid(),
    "question_id" UUID NOT NULL,
    "version" INTEGER NOT NULL,
    "content" JSONB NOT NULL,
    "options" JSONB NOT NULL,
    "source_draft_id" UUID,
    "status" "question_version_status" NOT NULL,
    "approved_by" UUID,
    "approved_at" TIMESTAMPTZ,
    "published_at" TIMESTAMPTZ,
    "created_at" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "question_versions_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "question_answers" (
    "id" UUID NOT NULL DEFAULT gen_random_uuid(),
    "question_version_id" UUID NOT NULL,
    "value" TEXT NOT NULL,
    "explanation" TEXT,
    "explanation_source" "explanation_source",
    "created_at" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "question_answers_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "question_classifications" (
    "id" UUID NOT NULL DEFAULT gen_random_uuid(),
    "question_version_id" UUID NOT NULL,
    "syllabus_node_id" UUID NOT NULL,
    "is_primary" BOOLEAN NOT NULL DEFAULT true,
    "status" "classification_status" NOT NULL,
    "classified_by" TEXT,
    "reviewed_by" UUID,
    "created_at" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updated_at" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "question_classifications_pkey" PRIMARY KEY ("id")
);

-- CreateIndex
CREATE UNIQUE INDEX "syllabuses_code_year_key" ON "syllabuses"("code", "year");

-- CreateIndex
CREATE INDEX "syllabus_nodes_syllabus_id_idx" ON "syllabus_nodes"("syllabus_id");

-- CreateIndex
CREATE INDEX "syllabus_nodes_parent_id_idx" ON "syllabus_nodes"("parent_id");

-- CreateIndex
CREATE UNIQUE INDEX "syllabus_nodes_syllabus_id_code_key" ON "syllabus_nodes"("syllabus_id", "code");

-- CreateIndex
CREATE INDEX "ingestion_batches_syllabus_id_idx" ON "ingestion_batches"("syllabus_id");

-- CreateIndex
CREATE INDEX "ingestion_batches_status_idx" ON "ingestion_batches"("status");

-- CreateIndex
CREATE INDEX "source_documents_batch_id_idx" ON "source_documents"("batch_id");

-- CreateIndex
CREATE INDEX "ingestion_runs_batch_id_idx" ON "ingestion_runs"("batch_id");

-- CreateIndex
CREATE INDEX "ingestion_runs_status_idx" ON "ingestion_runs"("status");

-- CreateIndex
CREATE INDEX "question_drafts_run_id_idx" ON "question_drafts"("run_id");

-- CreateIndex
CREATE INDEX "question_drafts_batch_id_idx" ON "question_drafts"("batch_id");

-- CreateIndex
CREATE INDEX "question_drafts_status_idx" ON "question_drafts"("status");

-- CreateIndex
CREATE UNIQUE INDEX "question_drafts_run_id_question_ref_key" ON "question_drafts"("run_id", "question_ref");

-- CreateIndex
CREATE INDEX "answer_records_run_id_idx" ON "answer_records"("run_id");

-- CreateIndex
CREATE INDEX "answer_records_batch_id_idx" ON "answer_records"("batch_id");

-- CreateIndex
CREATE INDEX "answer_records_question_ref_idx" ON "answer_records"("question_ref");

-- CreateIndex
CREATE UNIQUE INDEX "answer_records_run_id_question_ref_key" ON "answer_records"("run_id", "question_ref");

-- CreateIndex
CREATE INDEX "question_assets_draft_id_idx" ON "question_assets"("draft_id");

-- CreateIndex
CREATE INDEX "question_assets_source_document_id_idx" ON "question_assets"("source_document_id");

-- CreateIndex
CREATE INDEX "question_assets_role_idx" ON "question_assets"("role");

-- CreateIndex
CREATE INDEX "review_events_draft_id_idx" ON "review_events"("draft_id");

-- CreateIndex
CREATE INDEX "review_events_reviewer_id_idx" ON "review_events"("reviewer_id");

-- CreateIndex
CREATE UNIQUE INDEX "bank_questions_source_key_key" ON "bank_questions"("source_key");

-- CreateIndex
CREATE INDEX "bank_questions_subject_idx" ON "bank_questions"("subject");

-- CreateIndex
CREATE INDEX "question_versions_question_id_idx" ON "question_versions"("question_id");

-- CreateIndex
CREATE INDEX "question_versions_status_idx" ON "question_versions"("status");

-- CreateIndex
CREATE UNIQUE INDEX "question_versions_question_id_version_key" ON "question_versions"("question_id", "version");

-- CreateIndex
CREATE UNIQUE INDEX "question_answers_question_version_id_key" ON "question_answers"("question_version_id");

-- CreateIndex
CREATE INDEX "question_classifications_question_version_id_idx" ON "question_classifications"("question_version_id");

-- CreateIndex
CREATE INDEX "question_classifications_syllabus_node_id_idx" ON "question_classifications"("syllabus_node_id");

-- CreateIndex
CREATE INDEX "question_classifications_status_idx" ON "question_classifications"("status");

-- CreateIndex
CREATE UNIQUE INDEX "question_classifications_question_version_id_syllabus_node__key" ON "question_classifications"("question_version_id", "syllabus_node_id");

-- AddForeignKey
ALTER TABLE "syllabus_nodes" ADD CONSTRAINT "syllabus_nodes_syllabus_id_fkey" FOREIGN KEY ("syllabus_id") REFERENCES "syllabuses"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "syllabus_nodes" ADD CONSTRAINT "syllabus_nodes_parent_id_fkey" FOREIGN KEY ("parent_id") REFERENCES "syllabus_nodes"("id") ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "ingestion_batches" ADD CONSTRAINT "ingestion_batches_syllabus_id_fkey" FOREIGN KEY ("syllabus_id") REFERENCES "syllabuses"("id") ON DELETE SET NULL ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "source_documents" ADD CONSTRAINT "source_documents_batch_id_fkey" FOREIGN KEY ("batch_id") REFERENCES "ingestion_batches"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "ingestion_runs" ADD CONSTRAINT "ingestion_runs_batch_id_fkey" FOREIGN KEY ("batch_id") REFERENCES "ingestion_batches"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "question_drafts" ADD CONSTRAINT "question_drafts_run_id_fkey" FOREIGN KEY ("run_id") REFERENCES "ingestion_runs"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "answer_records" ADD CONSTRAINT "answer_records_run_id_fkey" FOREIGN KEY ("run_id") REFERENCES "ingestion_runs"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "question_assets" ADD CONSTRAINT "question_assets_draft_id_fkey" FOREIGN KEY ("draft_id") REFERENCES "question_drafts"("id") ON DELETE SET NULL ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "question_assets" ADD CONSTRAINT "question_assets_source_document_id_fkey" FOREIGN KEY ("source_document_id") REFERENCES "source_documents"("id") ON DELETE SET NULL ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "review_events" ADD CONSTRAINT "review_events_draft_id_fkey" FOREIGN KEY ("draft_id") REFERENCES "question_drafts"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "question_versions" ADD CONSTRAINT "question_versions_question_id_fkey" FOREIGN KEY ("question_id") REFERENCES "bank_questions"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "question_answers" ADD CONSTRAINT "question_answers_question_version_id_fkey" FOREIGN KEY ("question_version_id") REFERENCES "question_versions"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "question_classifications" ADD CONSTRAINT "question_classifications_question_version_id_fkey" FOREIGN KEY ("question_version_id") REFERENCES "question_versions"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "question_classifications" ADD CONSTRAINT "question_classifications_syllabus_node_id_fkey" FOREIGN KEY ("syllabus_node_id") REFERENCES "syllabus_nodes"("id") ON DELETE RESTRICT ON UPDATE CASCADE;

