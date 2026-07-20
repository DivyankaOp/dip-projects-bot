# Task Assistant Chatbot

Ek chatbot jo Hinglish mein baat karke:
- Naya task ek-ek sawal poochkar Supabase mein add karta hai
- Date-range ya overdue report generate karta hai
- Kisi bhi din ki leave requests batata hai

---

## STEP 1: Supabase set up karo

1. https://supabase.com par jaake free account banao, naya project banao.
2. Left sidebar mein **SQL Editor** kholo.
3. Is repo ki `supabase-schema.sql` file kholo, poora content copy karke SQL Editor mein paste karo, **Run** dabao.
   - Isse `departments`, `employees`, `projects`, `task_types`, `tasks`, `leaves` tables ban jayengi aur aapki di hui list (departments/employees/projects/task types) automatically bhar jayegi.
4. Left sidebar mein **Settings > API** kholo. Yahan se 2 cheezein copy karni hain:
   - **Project URL**
   - **service_role key** (secret wali, anon wali nahi)

---

## STEP 2: Anthropic API key lo

1. https://console.anthropic.com par account banao.
2. **API Keys** section mein naya key banao, copy kar lo.

---

## STEP 3: Code GitHub par daalo

Terminal mein (ya GitHub Desktop use karo):

```bash
cd task-chatbot
git init
git add .
git commit -m "Task assistant chatbot"
git branch -M main
git remote add origin https://github.com/<aapka-username>/<repo-name>.git
git push -u origin main
```

Agar terminal use nahi karna: GitHub.com par naya **empty repository** banao, phir "uploading an existing file" link se is poori folder ki files (node_modules chhod kar) drag-drop kar do.

---

## STEP 4: Vercel par deploy karo

1. https://vercel.com par jaake GitHub account se login karo.
2. **Add New > Project** dabao, jo repo abhi banaya usse select karo, **Import** dabao.
3. Deploy hone se pehle **Environment Variables** section mein yeh 3 add karo (STEP 1 aur 2 se li hui values):

   | Name | Value |
   |---|---|
   | `ANTHROPIC_API_KEY` | apni Anthropic key |
   | `SUPABASE_URL` | apna Supabase project URL |
   | `SUPABASE_SERVICE_ROLE_KEY` | apni Supabase service_role key |

4. **Deploy** dabao. 2 minute mein live link mil jayega (jaise `https://your-app.vercel.app`) — yeh link kisi ke saath bhi share karo, seedha browser mein chalega, koi login/setup nahi chahiye.

---

## Local par test karna ho toh (optional)

```bash
npm install
cp .env.example .env.local
# .env.local file kholke teeno keys bhar do
npm run dev
```

Phir http://localhost:3000 par khul jayega.

---

## Baad mein master data update karna ho (naya employee/project add karna)

Supabase Dashboard > **Table Editor** > `employees` (ya `departments`/`projects`/`task_types`) table kholo aur seedha row add kar do. Chatbot khud-ba-khud nayi list use karne lagega, code change karne ki zarurat nahi.
