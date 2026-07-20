import { supabase } from "@/lib/supabase";

export async function POST(req) {
  try {
    const formData = await req.formData();
    const file = formData.get("file");
    if (!file) return Response.json({ error: "No file" }, { status: 400 });

    const arrayBuffer = await file.arrayBuffer();
    const fileName = `${Date.now()}-${file.name}`;

    const { error } = await supabase.storage
      .from("task-files")
      .upload(fileName, Buffer.from(arrayBuffer), { contentType: file.type });

    if (error) return Response.json({ error: error.message }, { status: 500 });

    const { data } = supabase.storage.from("task-files").getPublicUrl(fileName);
    return Response.json({ url: data.publicUrl });
  } catch (err) {
    return Response.json({ error: err.message }, { status: 500 });
  }
}
