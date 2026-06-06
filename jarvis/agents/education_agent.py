"""Education agent — teaches students, creates quizzes, tracks progress."""

import json
from typing import Any

from jarvis.agents.base_agent import BaseAgent


class EducationAgent(BaseAgent):
    name = "education"
    description = "Teaches any subject, generates quizzes, solves problems step-by-step, and tracks student progress."

    def tools(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "teach_topic",
                "description": (
                    "Teaches a subject topic at the right level with explanation, "
                    "real-world examples, key points, and a practice question."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "subject": {"type": "string", "description": "e.g. Mathematics, Physics, Biology, History, Kiswahili"},
                        "topic": {"type": "string", "description": "e.g. Pythagoras Theorem, Photosynthesis, World War 2"},
                        "level": {
                            "type": "string",
                            "enum": ["primary", "secondary", "university", "professional"],
                            "description": "Student level. Default: secondary",
                        },
                        "student_name": {"type": "string", "description": "Student name (optional, for personalisation)"},
                        "language": {"type": "string", "description": "Language: English or Kiswahili. Default: English"},
                    },
                    "required": ["subject", "topic"],
                },
            },
            {
                "name": "create_quiz",
                "description": "Creates a quiz with multiple-choice and short-answer questions, including an answer key.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "subject": {"type": "string"},
                        "topic": {"type": "string"},
                        "num_questions": {"type": "integer", "description": "Number of questions (default 5, max 20)"},
                        "level": {"type": "string", "enum": ["primary", "secondary", "university"]},
                        "include_answers": {"type": "boolean", "description": "Include answer key (default true)"},
                    },
                    "required": ["subject", "topic"],
                },
            },
            {
                "name": "solve_problem",
                "description": "Solves any math or science problem step-by-step with full working shown.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "problem": {"type": "string", "description": "The exact problem to solve"},
                        "subject": {"type": "string", "description": "Subject area e.g. Algebra, Calculus, Chemistry"},
                        "level": {"type": "string"},
                    },
                    "required": ["problem"],
                },
            },
            {
                "name": "create_lesson_plan",
                "description": "Creates a full structured lesson plan following Kenya national curriculum standards.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "subject": {"type": "string"},
                        "topic": {"type": "string"},
                        "duration_minutes": {"type": "integer", "description": "Lesson duration (default 40 min)"},
                        "level": {"type": "string"},
                        "num_students": {"type": "integer", "description": "Class size"},
                        "learning_objectives": {"type": "string", "description": "Specific objectives (optional)"},
                    },
                    "required": ["subject", "topic"],
                },
            },
            {
                "name": "explain_concept",
                "description": "Explains a concept using simple language, analogies, and diagrams described in text.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "concept": {"type": "string"},
                        "subject": {"type": "string"},
                        "use_analogy": {"type": "boolean", "description": "Use a real-life analogy (default true)"},
                        "level": {"type": "string"},
                    },
                    "required": ["concept"],
                },
            },
            {
                "name": "track_student",
                "description": "Records a student's session results or retrieves their full progress history.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "student_name": {"type": "string"},
                        "action": {
                            "type": "string",
                            "enum": ["record", "retrieve", "list_all"],
                            "description": "record new result, retrieve one student's history, or list all students",
                        },
                        "subject": {"type": "string"},
                        "topic": {"type": "string"},
                        "score": {"type": "integer", "description": "Score out of 100"},
                        "notes": {"type": "string", "description": "Teacher notes or observations"},
                    },
                    "required": ["action"],
                },
            },
            {
                "name": "generate_flashcards",
                "description": "Generates study flashcards (question/answer pairs) for a topic.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "subject": {"type": "string"},
                        "topic": {"type": "string"},
                        "num_cards": {"type": "integer", "description": "Number of flashcards (default 10)"},
                        "level": {"type": "string"},
                    },
                    "required": ["subject", "topic"],
                },
            },
        ]

    def handle(self, tool_name: str, tool_input: dict[str, Any]) -> str:
        if tool_name == "teach_topic":       return self._teach(tool_input)
        if tool_name == "create_quiz":       return self._quiz(tool_input)
        if tool_name == "solve_problem":     return self._solve(tool_input)
        if tool_name == "create_lesson_plan": return self._lesson_plan(tool_input)
        if tool_name == "explain_concept":   return self._explain(tool_input)
        if tool_name == "track_student":     return self._track(tool_input)
        if tool_name == "generate_flashcards": return self._flashcards(tool_input)
        return f"Unknown tool: {tool_name}"

    def _teach(self, inp: dict) -> str:
        lang = inp.get("language", "English")
        student = inp.get("student_name", "")
        greeting = f"Addressing student: {student}. " if student else ""
        return json.dumps({
            "instruction": (
                f"{greeting}Teach '{inp['topic']}' ({inp['subject']}) "
                f"at {inp.get('level','secondary')} level in {lang}. "
                "Structure your response as: "
                "1) INTRODUCTION — hook with a real-life example from Kenya/East Africa. "
                "2) CORE EXPLANATION — clear, simple explanation. "
                "3) KEY POINTS — bullet list of what to remember. "
                "4) WORKED EXAMPLE — show a concrete example. "
                "5) COMMON MISTAKES — what students get wrong. "
                "6) PRACTICE QUESTION — one question for the student to try. "
                "Be encouraging and patient."
            ),
            "subject": inp["subject"],
            "topic": inp["topic"],
            "level": inp.get("level", "secondary"),
        })

    def _quiz(self, inp: dict) -> str:
        n = min(int(inp.get("num_questions", 5)), 20)
        return json.dumps({
            "instruction": (
                f"Create a {n}-question quiz on '{inp['topic']}' ({inp['subject']}) "
                f"at {inp.get('level','secondary')} level. "
                "Mix multiple-choice (A/B/C/D) and short-answer questions. "
                "Number each question clearly. "
                "At the end, provide a complete ANSWER KEY with brief explanations."
            ),
            "subject": inp["subject"],
            "topic": inp["topic"],
            "num_questions": n,
        })

    def _solve(self, inp: dict) -> str:
        return json.dumps({
            "instruction": (
                f"Solve this problem step by step: '{inp['problem']}'. "
                f"Subject: {inp.get('subject','General')}. "
                "Show EVERY step numbered. State what you are doing and why at each step. "
                "Box or highlight the final answer. "
                "After the solution, explain the method used in simple terms."
            ),
            "problem": inp["problem"],
        })

    def _lesson_plan(self, inp: dict) -> str:
        dur = int(inp.get("duration_minutes", 40))
        return json.dumps({
            "instruction": (
                f"Create a complete lesson plan for '{inp['topic']}' ({inp['subject']}), "
                f"{inp.get('level','secondary')} level, {dur} minutes, "
                f"class size: {inp.get('num_students',30)}. "
                "Follow Kenya CBC/8-4-4 curriculum standards. "
                "Include: Learning Objectives, Materials/Resources, "
                "Introduction/Hook (5min), Main Content Delivery, "
                "Student Activity/Group Work, Assessment/Questions, "
                "Homework Assignment, Teacher Notes."
            ),
            "subject": inp["subject"],
            "topic": inp["topic"],
            "duration_minutes": dur,
        })

    def _explain(self, inp: dict) -> str:
        return json.dumps({
            "instruction": (
                f"Explain '{inp['concept']}' ({inp.get('subject','')}) "
                f"at {inp.get('level','secondary')} level. "
                + ("Use a relatable real-life analogy from everyday Kenyan life. " if inp.get("use_analogy", True) else "")
                + "Use simple language. Describe any diagrams in text form with ASCII art if helpful."
            ),
            "concept": inp["concept"],
        })

    def _track(self, inp: dict) -> str:
        action = inp["action"]
        try:
            from jarvis.core.memory import MemoryManager
            mem = MemoryManager()

            if action == "list_all":
                summary = mem.student_summary()
                return json.dumps({"students": summary, "total": len(summary)})

            student = inp.get("student_name", "Unknown")

            if action == "record":
                mem.log_student(
                    student,
                    inp.get("subject", "General"),
                    inp.get("topic", ""),
                    inp.get("score"),
                    inp.get("notes", ""),
                )
                # Also store in main memory
                mem.remember("students", student, f"Last studied: {inp.get('subject')} — {inp.get('topic')}", importance=2)
                return json.dumps({"recorded": True, "student": student, "score": inp.get("score")})

            if action == "retrieve":
                progress = mem.get_student_progress(student)
                return json.dumps({"student": student, "sessions": len(progress), "progress": progress})

        except Exception as exc:
            return f"Progress tracking error: {exc}"
        return json.dumps({"error": "Unknown action"})

    def _flashcards(self, inp: dict) -> str:
        n = int(inp.get("num_cards", 10))
        return json.dumps({
            "instruction": (
                f"Generate {n} study flashcards for '{inp['topic']}' ({inp['subject']}) "
                f"at {inp.get('level','secondary')} level. "
                "Format each card as:\n"
                "CARD [N]\n"
                "Q: [question]\n"
                "A: [answer]\n\n"
                "Cover the most important concepts, definitions, and formulas."
            ),
            "subject": inp["subject"],
            "topic": inp["topic"],
            "num_cards": n,
        })
