-- ============================================================
-- Ye poora script Supabase Dashboard > SQL Editor mein paste
-- karke "Run" dabao. Ek hi baar chalana hai.
-- ============================================================

-- 1) MASTER / SITE DATA (departments, employees, projects, task types)
create table if not exists departments (
  id bigint generated always as identity primary key,
  name text not null unique
);

create table if not exists employees (
  id bigint generated always as identity primary key,
  name text not null unique
);

create table if not exists projects (
  id bigint generated always as identity primary key,
  name text not null unique
);

create table if not exists task_types (
  id bigint generated always as identity primary key,
  name text not null unique
);

-- 2) LEAVE DATA
create table if not exists leaves (
  id bigint generated always as identity primary key,
  employee_id bigint references employees(id),
  leave_date date not null,
  status text not null default 'Pending' check (status in ('Pending','Approved','Rejected')),
  reason text,
  created_at timestamptz not null default now()
);

-- 3) TASK DATA (dusri jagah, jaisa aapne kaha)
create table if not exists tasks (
  id bigint generated always as identity primary key,
  department_id bigint references departments(id),
  assigned_to bigint references employees(id),
  project_id bigint references projects(id),
  task_type_id bigint references task_types(id),
  description text not null,
  hours_to_complete numeric,
  target_date date not null,
  priority text not null check (priority in ('Low','Medium','High')),
  rescheduling_possible boolean not null default false,
  attachment_url text,
  voice_note_url text,
  status text not null default 'Open' check (status in ('Open','In Progress','Completed','Overdue')),
  created_at timestamptz not null default now()
);

-- Storage bucket for attachments / voice notes
insert into storage.buckets (id, name, public)
values ('task-files', 'task-files', true)
on conflict (id) do nothing;

-- ============================================================
-- SEED DATA - aapki di hui list se
-- ============================================================
insert into departments (name) values
  ('Engg. Division'), ('MDO OFFICE'), ('MIS Support'), ('Site')
on conflict (name) do nothing;

insert into employees (name) values
  ('Aayushi Shah'), ('Admin User'), ('Amit Kedariya'), ('Charmy Desai'),
  ('Chirag Shah'), ('Divyanka Patil'), ('Divyesh Rana'), ('Kishan Kalsariya'),
  ('Nisarg Pandya'), ('Viral Lad')
on conflict (name) do nothing;

insert into projects (name) values
  ('Internal Support'), ('Proposed Cafe Project'), ('SMJV Boys Hostel')
on conflict (name) do nothing;

insert into task_types (name) values
  ('abc'), ('Comparative'), ('df'), ('Drawing'), ('Estimate'), ('frg'),
  ('Site Visit'), ('Ticket Follow-up')
on conflict (name) do nothing;
