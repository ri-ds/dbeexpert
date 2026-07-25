"""
agents.py — the agent layer (from the notebook).

An LLM router selects ONE ontology agent per question (Research Agent
preferred, as in the original app). The selected agent then runs the
classify -> judge -> rank -> extract pipeline on the shared retriever.

Agent selection is domain routing: all agents share the same retriever, so
the pipeline (retrieval + judging + extraction) is common — the selected
agent's schema drives the router's choice.
"""

import openai
from schema_loader import load_all_schemas
from llm_utils import run_query, get_semaphore

client = openai.AsyncOpenAI()


class OntologyAgent:
    def __init__(self, name, entities, relations, potential_schema, retriever=None, choice=None):
        self.name = name
        self.entities = entities
        self.relations = relations
        self.potential_schema = potential_schema
        self.retriever = retriever
        self.choice = choice  # 1 = Hybrid, 2 = Vector (informational)

    def describe(self):
        ents = ", ".join(e if isinstance(e, str) else e.get("label", str(e)) for e in self.entities)
        rels = ", ".join(r if isinstance(r, str) else r.get("label", str(r)) for r in self.relations)
        return f"Agent: {self.name}\nEntities: {ents}\nRelations: {rels}"

    async def query(self, question):
        # The selected agent runs the full retrieval + judging + extraction pipeline.
        return await run_query(question, self.retriever)


class GraphRAGOrchestrator:
    """LLM selects a single agent per question (mirrors the original app)."""

    def __init__(self, agents, selector_model="gpt-5-mini"):
        self.agents = agents
        self.selector_model = selector_model

    async def select_agent(self, question):
        agent_descriptions = "\n\n".join(a.describe() for a in self.agents)
        selector_prompt = f"""You are an intelligent router. Based on the user query and the agent
descriptions, select the SINGLE most relevant agent to answer the question.

Query:
{question}

Agent Descriptions:
{agent_descriptions}

Return ONLY the exact agent name, nothing else.
"""
        async with get_semaphore():
            response = await client.chat.completions.create(
                model=self.selector_model,
                messages=[
                    {"role": "system", "content": "You are a helpful routing assistant."},
                    {"role": "user", "content": selector_prompt},
                ],
            )
        raw = response.choices[0].message.content.strip()
        selected_names = [n.strip() for n in raw.replace("\n", ",").split(",") if n.strip()]

        # Prefer Research Agent if the router included it (as in the original code).
        if any(n.lower() == "research agent" for n in selected_names):
            for a in self.agents:
                if a.name == "Research Agent":
                    return a

        # Otherwise take the first selected name that matches an agent.
        for n in selected_names:
            for a in self.agents:
                if a.name.lower() == n.lower():
                    return a

        # Loose fallback: substring match against the raw response.
        for a in self.agents:
            if a.name.lower() in raw.lower():
                return a

        return self.agents[0]

    async def route_query(self, question):
        agent = await self.select_agent(question)
        print(f"-> Selected agent: {agent.name}")
        answer = await agent.query(question)
        return agent.name, answer


def build_agents(retriever, choice=1):
    """choice: 1 = Hybrid, 2 = Vector."""
    schemas = load_all_schemas()
    return [
        OntologyAgent(name, s["entities"], s["relations"], s["potential_schema"],
                      retriever=retriever, choice=choice)
        for name, s in schemas.items()
    ]


async def run_agentic_query(question, retriever, choice=1):
    """Single entry point: LLM picks one agent, that agent runs the pipeline.

    Returns (selected_agent_name, results_list).
    """
    agents = build_agents(retriever, choice)
    orchestrator = GraphRAGOrchestrator(agents)
    return await orchestrator.route_query(question)
