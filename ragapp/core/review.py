from ragapp.core.drafts import DraftManager
from ragapp.core.approval import ApprovalEngine

def get_review(store): return DraftManager(store).list()
def approve(store,draft_id): return ApprovalEngine(store).approve(draft_id)
def reject(store,draft_id,reason): return ApprovalEngine(store).reject(draft_id,reason)
