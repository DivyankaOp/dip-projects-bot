import Anthropic from "@anthropic-ai/sdk";
import { toolDefinitions, runTool } from "@/lib/anthropic-tools";

const anthropic = new Anthropic({ apiKey: process.env.ANTHROPIC_API_KEY });

const SYSTEM_PROMPT = `Tum ek task-management assistant ho jo Hinglish (Hindi + English mix, Roman script) mein baat karta hai, bilkul user ke tone mein.

TUMHARE KAAM:

1. NAYA TASK ADD KARNA:
Jab user bole "task add karna hai" ya isse milta julta, toh in fields ko EK-EK KARKE, ek-ek sawal poochkar collect karo (sab ek saath mat pucho):
Department -> Assign to (employee) -> Project -> Task type -> Task description -> Hours to complete (optional, pucho par skip allowed) -> Target date -> Priority (Low/Medium/High) -> Rescheduling possible (Yes/No) -> Attachment (optional) -> Voice note (optional).
Department/Employee/Project/Task type poochne se pehle get_master_data tool call karke valid options user ko dikhao (list ke roop mein), taaki user usi mein se chune. Jo bhi user bole use in options se match karo - agar match na ho toh dubara options dikha kar pucho.
Jab saari zaroori fields mil jayein (attachment/voice note optional hain, ek baar pooch kar skip bhi kar sakte ho), create_task tool call karo. Success milne par user ko confirm karo ki task ban gaya.

2. REPORTS:
User agar "is date se is date tak report do" bole toh get_report tool ko report_type='date_range' ke saath start_date aur end_date bhejo.
Agar "overdue" bole (date ke saath ya bina) toh report_type='overdue' bhejo - date ki zarurat nahi, aaj tak ke saare overdue tasks aa jayenge.
Result ko clean table/list format mein Hinglish mein present karo.

3. LEAVE QUERIES:
"aaj koi leave hai kya", "kiski leave request hai" jaise sawalon par get_leave_status tool call karo (date na di ho toh aaj ki date use hogi, tool khud kar lega).
Result clearly batao - kiski leave hai, status kya hai (Pending/Approved/Rejected).

Hamesha friendly, seedha, concise Hinglish mein jawab do. Ek time par sirf ek sawal pucho jab task add kar rahe ho.`;

export async function POST(req) {
  try {
    const { messages } = await req.json();

    let conversation = messages;
    let finalText = "";
    let guard = 0;

    while (guard < 6) {
      guard++;
      const response = await anthropic.messages.create({
        model: "claude-sonnet-4-6",
        max_tokens: 1500,
        system: SYSTEM_PROMPT,
        tools: toolDefinitions,
        messages: conversation
      });

      const toolUses = response.content.filter((b) => b.type === "tool_use");
      const textBlocks = response.content.filter((b) => b.type === "text");
      finalText = textBlocks.map((b) => b.text).join("\n");

      if (toolUses.length === 0) {
        conversation = [...conversation, { role: "assistant", content: response.content }];
        break;
      }

      conversation = [...conversation, { role: "assistant", content: response.content }];

      const toolResults = [];
      for (const tu of toolUses) {
        const result = await runTool(tu.name, tu.input);
        toolResults.push({
          type: "tool_result",
          tool_use_id: tu.id,
          content: JSON.stringify(result)
        });
      }
      conversation = [...conversation, { role: "user", content: toolResults }];
    }

    return Response.json({ reply: finalText, messages: conversation });
  } catch (err) {
    console.error(err);
    return Response.json({ error: err.message || "Kuch galat ho gaya" }, { status: 500 });
  }
}
