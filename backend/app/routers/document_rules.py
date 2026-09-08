"""Phase 11 API: immutable document-list versions and safe personal rules."""
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.orm import Session
from app.db import get_db
from app.deps import get_current_user, require_roles
from app.models import Candidate, User, UserRole
from app.document_rules import DocumentList, DocumentListVersion, DocumentListItem, CandidateDocumentSet, CandidateDocumentStatus, PersonalRule
router=APIRouter(prefix='/document-lists',tags=['document-lists'])
class ItemIn(BaseModel): key:str=Field(pattern=r'^[a-z][a-z0-9_]{1,79}$'); title_ru:str=Field(min_length=1,max_length=200); explanation:str|None=None; required:bool=True
class ListIn(BaseModel): name:str=Field(min_length=1,max_length=160); description:str|None=None; position:str|None=None; stage:str|None=None; items:list[ItemIn]=Field(min_length=1)
class StatusIn(BaseModel): status:str=Field(pattern='^(missing|received)$'); row_version:int=Field(ge=1)
class RuleIn(BaseModel): name:str=Field(min_length=1,max_length=160); trigger:str; action:str; parameters:dict={}

def _admin(u:User=Depends(require_roles(UserRole.ADMIN))): return u
@router.post('',status_code=201)
def create_list(body:ListIn,db:Session=Depends(get_db),u:User=Depends(_admin)):
    l=DocumentList(name=body.name,description=body.description,position=body.position,stage=body.stage,owner_user_id=u.id); db.add(l); db.flush(); v=DocumentListVersion(list_id=l.id,version=1,state='draft',created_by_user_id=u.id); db.add(v); db.flush()
    db.add_all([DocumentListItem(version_id=v.id,item_key=i.key,title_ru=i.title_ru,explanation=i.explanation,required=i.required,sort_order=n) for n,i in enumerate(body.items)]); db.commit(); return {'id':str(l.id),'version_id':str(v.id),'version':1,'state':'draft'}
@router.get('')
def lists(db:Session=Depends(get_db),u:User=Depends(get_current_user)):
    rows=db.scalars(select(DocumentList).order_by(DocumentList.name)).all(); return [{'id':str(x.id),'name':x.name,'position':x.position,'stage':x.stage} for x in rows if x.owner_user_id==u.id or u.role==UserRole.ADMIN]
@router.post('/{list_id}/versions')
def new_version(list_id:UUID,body:ListIn,db:Session=Depends(get_db),u:User=Depends(_admin)):
    l=db.get(DocumentList,list_id)
    if not l: raise HTTPException(404,'Список не найден')
    n=(db.scalar(select(DocumentListVersion.version).where(DocumentListVersion.list_id==list_id).order_by(DocumentListVersion.version.desc())) or 0)+1
    v=DocumentListVersion(list_id=list_id,version=n,state='draft',created_by_user_id=u.id); db.add(v); db.flush(); db.add_all([DocumentListItem(version_id=v.id,item_key=i.key,title_ru=i.title_ru,explanation=i.explanation,required=i.required,sort_order=k) for k,i in enumerate(body.items)]); db.commit(); return {'version_id':str(v.id),'version':n,'state':'draft'}
@router.post('/versions/{version_id}/publish')
def publish(version_id:UUID,db:Session=Depends(get_db),u:User=Depends(_admin)):
    v=db.get(DocumentListVersion,version_id)
    if not v or v.state!='draft': raise HTTPException(409,'Публиковать можно только draft')
    db.execute(update(DocumentListVersion).where(DocumentListVersion.list_id==v.list_id,DocumentListVersion.state=='published').values(state='archived'))
    v.state='published'; db.commit(); return {'version':v.version,'state':v.state}
@router.post('/candidates/{candidate_id}/apply')
def apply(candidate_id:UUID,version_id:UUID,db:Session=Depends(get_db),u:User=Depends(get_current_user)):
    c=db.get(Candidate,candidate_id)
    if not c or c.deleted_at or (u.role==UserRole.HR and c.owner_user_id!=u.id): raise HTTPException(404,'Кандидат не найден')
    v=db.get(DocumentListVersion,version_id)
    if not v or v.state!='published': raise HTTPException(409,'Нужна опубликованная версия')
    s=CandidateDocumentSet(candidate_id=c.id,list_id=v.list_id,version_id=v.id); db.add(s); db.flush(); items=db.scalars(select(DocumentListItem).where(DocumentListItem.version_id==v.id).order_by(DocumentListItem.sort_order)).all(); db.add_all([CandidateDocumentStatus(set_id=s.id,item_key=i.item_key,changed_by_user_id=u.id) for i in items]); db.commit(); return {'set_id':str(s.id),'version':v.version,'missing_required':[i.item_key for i in items if i.required]}
@router.patch('/statuses/{status_id}')
def change_status(status_id:UUID,body:StatusIn,db:Session=Depends(get_db),u:User=Depends(get_current_user)):
    row=db.get(CandidateDocumentStatus,status_id)
    if not row or row.row_version!=body.row_version: raise HTTPException(409,'Конфликт версии')
    row.status=body.status; row.row_version+=1; row.changed_by_user_id=u.id; db.commit(); return {'status':row.status,'row_version':row.row_version}
@router.get('/rules')
def rules(db:Session=Depends(get_db),u:User=Depends(get_current_user)): return db.scalars(select(PersonalRule).where(PersonalRule.owner_user_id==u.id)).all()
@router.post('/rules',status_code=201)
def create_rule(body:RuleIn,db:Session=Depends(get_db),u:User=Depends(get_current_user)):
    if body.trigger not in ('stage_entered','documents_due') or body.action not in ('apply_list','document_request','document_reminder'): raise HTTPException(422,'Недопустимый закрытый вариант')
    if body.action=='document_reminder' and not isinstance(body.parameters.get('days'),int) or (body.action=='document_reminder' and not 1<=body.parameters['days']<=30): raise HTTPException(422,'Срок должен быть от 1 до 30 дней')
    r=PersonalRule(owner_user_id=u.id,name=body.name,trigger=body.trigger,action=body.action,parameters=body.parameters); db.add(r); db.commit(); return r
