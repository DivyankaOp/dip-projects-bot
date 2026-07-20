import { createClient } from "@supabase/supabase-js";

let _supabase = null;

export function getSupabase() {
  if (_supabase) return _supabase;
  const url = process.env.SUPABASE_URL;
  const key = process.env.SUPABASE_SERVICE_ROLE_KEY;
  if (!url || !key) {
    throw new Error(
      "SUPABASE_URL ya SUPABASE_SERVICE_ROLE_KEY set nahi hai. Vercel > Settings > Environment Variables mein add karo."
    );
  }
  _supabase = createClient(url, key);
  return _supabase;
}
