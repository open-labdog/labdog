"""Tests for GitOps Celery task configuration."""

import pytest

pytestmark = pytest.mark.integration


class TestPipeline:
    def test_task_imports(self):
        """process_gitops_webhook task has the correct Celery task name."""
        from app.tasks.gitops import process_gitops_webhook

        assert process_gitops_webhook.name == "gitops.process_webhook"

    def test_task_has_retries(self):
        """process_gitops_webhook has max_retries=3."""
        from app.tasks.gitops import process_gitops_webhook

        assert process_gitops_webhook.max_retries == 3

    def test_task_has_retry_delay(self):
        """process_gitops_webhook has default_retry_delay=30."""
        from app.tasks.gitops import process_gitops_webhook

        assert process_gitops_webhook.default_retry_delay == 30

    def test_task_is_bound(self):
        """process_gitops_webhook is a bound task (bind=True in decorator)."""
        import inspect

        from app.tasks.gitops import process_gitops_webhook

        source = inspect.getsource(process_gitops_webhook)
        assert "bind=True" in source or "bind = True" in source
