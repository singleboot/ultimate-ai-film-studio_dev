from typing import Dict, List, Optional, Callable
from pathlib import Path
import json


class ApprovalWorkflow:
    def __init__(self):
        self.queue = []
        self.current_index = 0
        self.history = []
        self.max_retries = 5
        self.on_approve_callback = None
        self.on_reject_callback = None
        self.on_skip_callback = None

    def set_callbacks(self, on_approve: Callable = None, on_reject: Callable = None, on_skip: Callable = None):
        """Set callback functions for approval actions."""
        self.on_approve_callback = on_approve
        self.on_reject_callback = on_reject
        self.on_skip_callback = on_skip

    def load_items(self, items: List[Dict]):
        """Load items for approval workflow."""
        self.queue = []
        self.current_index = 0
        self.history = []

        for idx, item in enumerate(items):
            self.queue.append({
                "id": idx,
                "data": item.get("data"),
                "prompt": item.get("prompt"),
                "status": "pending",
                "retries": 0,
                "output_path": None
            })

    def add_item(self, data: Dict, prompt: str = ""):
        """Add a single item to the queue."""
        item = {
            "id": len(self.queue),
            "data": data,
            "prompt": prompt,
            "status": "pending",
            "retries": 0,
            "output_path": None
        }
        self.queue.append(item)

    def get_current(self) -> Optional[Dict]:
        """Get the current item for approval."""
        if self.current_index >= len(self.queue):
            return None

        current = self.queue[self.current_index]
        if current.get("status") == "completed":
            self.next()
            return self.get_current()

        return current

    def approve(self, output_path: str = None) -> Dict:
        """Approve the current item."""
        if self.current_index >= len(self.queue):
            return {"success": False, "error": "No more items"}

        current = self.queue[self.current_index]
        current["status"] = "approved"
        current["output_path"] = output_path

        self.history.append({
            "item_id": current["id"],
            "action": "approve",
            "output_path": output_path
        })

        result = {"success": True, "item": current}

        if self.on_approve_callback:
            callback_result = self.on_approve_callback(current)
            if callback_result:
                result["callback_result"] = callback_result

        self.next()
        return result

    def reject(self, reason: str = "") -> Dict:
        """Reject the current item and schedule for regeneration."""
        if self.current_index >= len(self.queue):
            return {"success": False, "error": "No more items"}

        current = self.queue[self.current_index]
        current["retries"] += 1

        if current["retries"] >= self.max_retries:
            current["status"] = "max_retries_exceeded"
            self.history.append({
                "item_id": current["id"],
                "action": "reject",
                "reason": reason,
                "status": "max_retries_exceeded"
            })
            self.next()
            return {
                "success": False,
                "error": f"Max retries ({self.max_retries}) exceeded",
                "item": current
            }

        current["status"] = "pending"
        current["reject_reason"] = reason

        self.history.append({
            "item_id": current["id"],
            "action": "reject",
            "reason": reason,
            "retries": current["retries"]
        })

        result = {"success": True, "item": current, "needs_regeneration": True}

        if self.on_reject_callback:
            callback_result = self.on_reject_callback(current)
            if callback_result:
                result["callback_result"] = callback_result

        return result

    def skip(self) -> Dict:
        """Skip the current item without saving."""
        if self.current_index >= len(self.queue):
            return {"success": False, "error": "No more items"}

        current = self.queue[self.current_index]
        current["status"] = "skipped"

        self.history.append({
            "item_id": current["id"],
            "action": "skip"
        })

        result = {"success": True, "item": current}

        if self.on_skip_callback:
            callback_result = self.on_skip_callback(current)
            if callback_result:
                result["callback_result"] = callback_result

        self.next()
        return result

    def next(self):
        """Move to the next item."""
        self.current_index += 1

    def previous(self):
        """Go back to the previous item."""
        if self.current_index > 0:
            self.current_index -= 1

    def get_progress(self) -> Dict:
        """Get current progress."""
        total = len(self.queue)
        completed = 0
        approved = 0
        rejected = 0
        skipped = 0

        for item in self.queue:
            if item.get("status") in ["approved", "completed"]:
                approved += 1
            elif item.get("status") == "rejected":
                rejected += 1
            elif item.get("status") == "skipped":
                skipped += 1

        completed = approved + rejected + skipped

        return {
            "total": total,
            "completed": completed,
            "current_index": self.current_index,
            "approved": approved,
            "rejected": rejected,
            "skipped": skipped,
            "remaining": total - completed,
            "is_complete": self.current_index >= total
        }

    def get_history(self) -> List[Dict]:
        """Get approval history."""
        return self.history

    def reset(self):
        """Reset the workflow."""
        self.current_index = 0
        for item in self.queue:
            item["status"] = "pending"
            item["retries"] = 0
        self.history = []

    def get_all_approved(self) -> List[Dict]:
        """Get all approved items."""
        return [item for item in self.queue if item.get("status") == "approved"]

    def get_all_rejected(self) -> List[Dict]:
        """Get all rejected items."""
        return [item for item in self.queue if item.get("status") in ["rejected", "max_retries_exceeded"]]

    def export_state(self) -> str:
        """Export current state as JSON."""
        state = {
            "current_index": self.current_index,
            "queue_size": len(self.queue),
            "progress": self.get_progress(),
            "history": self.history
        }
        return json.dumps(state, indent=2)