# Pipeline orchestration lives in tasks/gitops.py (the Celery task).
# Extracting helpers here would add indirection without value since
# the task directly calls importer.py and git_service.py.
