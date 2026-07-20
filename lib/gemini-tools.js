import { getSupabase } from "./supabase";

// ---- Tool schemas jo Gemini ko diye jaate hain ----
export const toolDefinitions = [
  {
    name: "get_master_data",
    description:
      "Departments, employees, projects, aur task types ki list laata hai. Task add karne se pehle ya options confirm karne ke liye use karo.",
    parametersJsonSchema: { type: "object", properties: {} }
  },
  {
    name: "create_task",
    description:
      "Naya task Supabase mein create karta hai. Sirf tabhi call karo jab department, assigned employee, project, task type, description, target date, aur priority - sab user se ek-ek karke confirm ho chuke ho.",
    parametersJsonSchema: {
      type: "object",
      properties: {
        department_name: { type: "string" },
        assigned_to_name: { type: "string" },
        project_name: { type: "string" },
        task_type_name: { type: "string" },
        description: { type: "string" },
        hours_to_complete: { type: "number" },
        target_date: { type: "string", description: "YYYY-MM-DD format" },
        priority: { type: "string", enum: ["Low", "Medium", "High"] },
        rescheduling_possible: { type: "boolean" },
        attachment_url: { type: "string" },
        voice_note_url: { type: "string" }
      },
      required: [
        "department_name",
        "assigned_to_name",
        "project_name",
        "task_type_name",
        "description",
        "target_date",
        "priority"
      ]
    }
  },
  {
    name: "get_report",
    description:
      "Tasks ki report nikalta hai. 'overdue' report maangi jaaye toh report_type='overdue' bhejo (dates ki zaroorat nahi, aaj tak ke overdue tasks aa jayenge). Date-range report maangi jaaye toh report_type='date_range' ke saath start_date aur end_date bhejo.",
    parametersJsonSchema: {
      type: "object",
      properties: {
        report_type: { type: "string", enum: ["overdue", "date_range", "all"] },
        start_date: { type: "string", description: "YYYY-MM-DD" },
        end_date: { type: "string", description: "YYYY-MM-DD" }
      },
      required: ["report_type"]
    }
  },
  {
    name: "get_leave_status",
    description:
      "Kisi date ke leave requests check karta hai - kiski leave hai, pending hai ya approved. Date na di jaaye toh aaj ki date use karo.",
    parametersJsonSchema: {
      type: "object",
      properties: {
        date: { type: "string", description: "YYYY-MM-DD, agar khaali ho toh aaj ki date use hogi" }
      }
    }
  }
];

function todayStr() {
  return new Date().toISOString().slice(0, 10);
}

// ---- Actual execution ----
export async function runTool(name, input) {
  if (name === "get_master_data") {
    const [d, e, p, t] = await Promise.all([
      getSupabase().from("departments").select("name"),
      getSupabase().from("employees").select("name"),
      getSupabase().from("projects").select("name"),
      getSupabase().from("task_types").select("name")
    ]);
    return {
      departments: (d.data || []).map((r) => r.name),
      employees: (e.data || []).map((r) => r.name),
      projects: (p.data || []).map((r) => r.name),
      task_types: (t.data || []).map((r) => r.name)
    };
  }

  if (name === "create_task") {
    const lookup = async (table, value) => {
      const { data, error } = await getSupabase()
        .from(table)
        .select("id")
        .ilike("name", value)
        .maybeSingle();
      if (error || !data) return null;
      return data.id;
    };

    const [department_id, assigned_to, project_id, task_type_id] = await Promise.all([
      lookup("departments", input.department_name),
      lookup("employees", input.assigned_to_name),
      lookup("projects", input.project_name),
      lookup("task_types", input.task_type_name)
    ]);

    if (!department_id || !assigned_to || !project_id || !task_type_id) {
      return {
        success: false,
        error:
          "Ek ya zyada values master list se match nahi hui (department/employee/project/task type). Sahi option se dubara pucho."
      };
    }

    const { data, error } = await getSupabase()
      .from("tasks")
      .insert({
        department_id,
        assigned_to,
        project_id,
        task_type_id,
        description: input.description,
        hours_to_complete: input.hours_to_complete || null,
        target_date: input.target_date,
        priority: input.priority,
        rescheduling_possible: !!input.rescheduling_possible,
        attachment_url: input.attachment_url || null,
        voice_note_url: input.voice_note_url || null
      })
      .select("id")
      .single();

    if (error) return { success: false, error: error.message };
    return { success: true, task_id: data.id };
  }

  if (name === "get_report") {
    let query = getSupabase()
      .from("tasks")
      .select(
        "id, description, target_date, priority, status, departments(name), employees:assigned_to(name), projects(name), task_types(name)"
      );

    if (input.report_type === "overdue") {
      query = query.lt("target_date", todayStr()).neq("status", "Completed");
    } else if (input.report_type === "date_range") {
      if (input.start_date) query = query.gte("target_date", input.start_date);
      if (input.end_date) query = query.lte("target_date", input.end_date);
    }

    const { data, error } = await query.order("target_date", { ascending: true });
    if (error) return { success: false, error: error.message };
    return { success: true, count: data.length, tasks: data };
  }

  if (name === "get_leave_status") {
    const date = input.date || todayStr();
    const { data, error } = await getSupabase()
      .from("leaves")
      .select("id, leave_date, status, reason, employees(name)")
      .eq("leave_date", date);

    if (error) return { success: false, error: error.message };
    return { success: true, date, count: data.length, leaves: data };
  }

  return { success: false, error: "Unknown tool: " + name };
}
