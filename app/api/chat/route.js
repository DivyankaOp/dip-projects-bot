import { GoogleGenAI } from "@google/genai";
import { toolDefinitions, runTool } from "@/lib/gemini-tools";

const ai = new GoogleGenAI({ apiKey: process.env.GEMINI_API_KEY });

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

    let contents = messages.map((m) => ({
      role: m.role === "assistant" ? "model" : "user",
      parts: [{ text: typeof m.content === "string" ? m.content : JSON.stringify(m.content) }]
    }));

    let finalText = "";
    let guard = 0;

    while (guard < 6) {
      guard++;
      const response = await ai.models.generateContent({
        model: "gemini-2.5-flash",
        contents,
        config: {
          systemInstruction: SYSTEM_PROMPT,
          tools: [{ functionDeclarations: toolDefinitions }]
        }
      });

      const calls = response.functionCalls || [];
      const modelParts = response.candidates?.[0]?.content?.parts || [{ text: response.text || "" }];

      contents.push({ role: "model", parts: modelParts });

      if (calls.length === 0) {
        finalText = response.text || "";
        break;
      }

      const responseParts = [];
      for (const call of calls) {
        const result = await runTool(call.name, call.args || {});
        responseParts.push({
          functionResponse: {
            name: call.name,
            response: result
          }
        });
      }
      contents.push({ role: "user", parts: responseParts });
    }

    return Response.json({ reply: finalText });
  } catch (err) {
    console.error(err);
    return Response.json({ error: err.message || "Kuch galat ho gaya" }, { status: 500 });
  }
}
