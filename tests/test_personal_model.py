from pathlib import Path

from meemee.personal_model import PersonalItemInput, PersonalModelStore


def item(value="Prefers concise answers", source="m1", confidence=0.8):
    return PersonalItemInput(kind="preference",title="answer length",value=value,confidence=confidence,source_id="gmail",source_record_id=source)


def test_personal_model_requires_evidence_and_is_owner_scoped(tmp_path: Path):
    store=PersonalModelStore(tmp_path/'personal.db')
    row=store.upsert('udita',item())
    assert row['evidence'][0]['source_record_id']=='m1'
    assert store.list('mallory')==[]


def test_same_claim_merges_evidence_and_confidence(tmp_path: Path):
    store=PersonalModelStore(tmp_path/'personal.db')
    first=store.upsert('udita',item())
    second=store.upsert('udita',item(source='m2',confidence=0.95))
    assert first['id']==second['id'] and second['confidence']==0.95
    assert len(second['evidence'])==2


def test_conflicting_claim_supersedes_without_erasing_history(tmp_path: Path):
    store=PersonalModelStore(tmp_path/'personal.db')
    old=store.upsert('udita',item())
    new=store.upsert('udita',item('Prefers detailed answers','m3'))
    assert new['supersedes_id']==old['id']
    assert store.get('udita',old['id'])['status']=='superseded'
    assert len(store.list('udita',include_history=True))==2


def test_user_delete_is_scoped_and_auditable(tmp_path: Path):
    store=PersonalModelStore(tmp_path/'personal.db'); row=store.upsert('udita',item())
    assert not store.delete('mallory',row['id'])
    assert store.delete('udita',row['id'])
    assert store.get('udita',row['id'])['status']=='deleted'

import json

import pytest

from meemee.agent import Agent
from meemee.memory import MemoryStore
from meemee.tools.base import ToolRegistry
from meemee.types import AgentDecision


class Model:
    async def decide(self, messages):
        self.messages=messages
        return AgentDecision(final="ok")


@pytest.mark.asyncio
async def test_agent_receives_owner_scoped_personal_model(tmp_path: Path):
    store=PersonalModelStore(tmp_path/'personal.db');store.upsert('udita',item())
    model=Model();await Agent(model,ToolRegistry(),MemoryStore(tmp_path/'memory.db'),personal_model=store).run('help',owner_id='udita')
    payload=json.loads(model.messages[1]['content'])
    assert payload['evidence_backed_personal_model'][0]['value']=='Prefers concise answers'

from meemee.context import ContextRecord, ContextStore
from meemee.reflection import PersonalModelReflector


class ReflectionModel:
    def __init__(self, payload): self.payload=payload
    async def chat(self, messages, temperature=0.1, max_tokens=None): self.messages=messages; return json.dumps(self.payload)


@pytest.mark.asyncio
async def test_reflection_accepts_only_claims_citing_supplied_evidence(tmp_path: Path):
    context=ContextStore(tmp_path/'context.db');context.register_source('udita','mail','gmail',{})
    context.ingest(ContextRecord('udita','mail','m1','document','Project','I am building Atlas','2026-09-22T10:00:00Z',{'message_id':'m1'}))
    personal=PersonalModelStore(tmp_path/'personal.db')
    model=ReflectionModel({'claims':[
        {'kind':'project','title':'Atlas','value':'Building Atlas','confidence':0.9,'source_id':'mail','source_record_id':'m1'},
        {'kind':'goal','title':'Unsupported','value':'Invented','confidence':1,'source_id':'mail','source_record_id':'missing'}]})
    result=await PersonalModelReflector(context,personal,model).reflect('udita')
    assert result['accepted']==1 and result['rejected']==1
    assert personal.list('udita')[0]['value']=='Building Atlas'


def test_expiry_supersedes_stale_claims(tmp_path: Path):
    store=PersonalModelStore(tmp_path/'personal.db')
    row=store.upsert('udita',PersonalItemInput(kind='routine',title='gym',value='Mondays',source_id='calendar',source_record_id='e1',valid_until='2026-01-01T00:00:00+00:00'))
    assert store.expire('udita',at='2026-09-22T00:00:00+00:00')==1
    assert store.get('udita',row['id'])['status']=='superseded'
