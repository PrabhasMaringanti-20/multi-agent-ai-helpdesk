import { apiClient } from "@/shared/api/client";
import { toast } from "@/shared/store/uiStore";

export function FeedbackButtons({ conversationId }: { conversationId?: string }) {
  const submit = async (rating: "up" | "down") => {
    if (!conversationId) return;
    try {
      await apiClient.post("/feedback", { conversation_id: conversationId, rating });
      toast.success("Thanks for the feedback!");
    } catch {
      toast.error("Could not submit feedback.");
    }
  };

  return (
    <div className="mt-2 flex gap-2 text-xs">
      <button className="text-slate-400 hover:text-green-600" onClick={() => submit("up")}>
        👍 Helpful
      </button>
      <button className="text-slate-400 hover:text-red-600" onClick={() => submit("down")}>
        👎 Not helpful
      </button>
    </div>
  );
}
