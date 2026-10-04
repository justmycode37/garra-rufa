import test, { after } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { randomBytes } from 'node:crypto';
import * as store from '../src/lib/store';
import * as community from '../src/lib/conditions';

const temporary = mkdtempSync(path.join(tmpdir(), 'garra-community-tests-'));
process.env.GARRA_DATA_DIR = temporary;
process.env.DATA_ENCRYPTION_KEY = randomBytes(32).toString('hex');
const legacy = store.createGuest('patient');
store.db().prepare('INSERT INTO community VALUES(?,?,?,?)').run(legacy.id, 'Legacy member', 'fabry', 'Previously opted in');
after(() => { store.db().close(); rmSync(temporary, { recursive: true, force: true }); });

test('legacy opt-in profiles migrate once and never rejoin after leaving', async () => {
  assert.equal(community.getCommunityProfile(legacy.id, 'fabry')?.alias, 'Legacy member');
  community.leaveCommunity(legacy.id, 'fabry');
  assert.equal(community.getCommunityProfile(legacy.id, 'fabry'), null);
  // Recreate an obsolete row to simulate code from an earlier server process.
  store.db().prepare('INSERT INTO community VALUES(?,?,?,?)').run(legacy.id, 'Old member', 'fabry', 'Old profile');
  const freshModule = await import(`../src/lib/conditions.ts?migration=${Date.now()}`) as typeof community;
  assert.equal(freshModule.getCommunityProfile(legacy.id, 'fabry'), null);
  assert.ok(store.db().prepare("SELECT name FROM condition_migrations WHERE name='memberships-v1'").get());
});

test('condition spelling variants and curated aliases resolve to one community while subtypes stay distinct', () => {
  const user = store.createGuest('patient');
  const first = community.saveRecordWithCondition(user.id, { kind: 'note', title: 'Condition note', conditionName: '  Test-Rare   Syndrome  ' });
  const second = community.saveRecordWithCondition(user.id, { kind: 'note', title: 'Same condition', conditionName: 'TEST RARE SYNDROME' });
  const third = community.saveRecordWithCondition(user.id, { kind: 'note', title: 'Unicode variant', conditionName: 'Ｔｅｓｔ rare syndrome' });
  assert.equal(first.diseaseId, second.diseaseId);
  assert.equal(first.diseaseId, third.diseaseId);
  assert.equal(community.listConditions().filter(item => item.name === 'Test-Rare Syndrome').length, 1);
  for (const name of ['FABRY', 'Fabry disease', 'Fabry-disease']) assert.equal(community.resolveCondition({ conditionName: name }, user.id)?.id, 'fabry');
  assert.equal(community.resolveCondition({ conditionName: 'Ehlers Danlos syndrome' }, user.id)?.id, 'eds');
  assert.equal(community.resolveCondition({ conditionName: 'Huntington’s disease' }, user.id)?.id, 'huntington');
  const type1 = community.resolveCondition({ conditionName: 'Test syndrome type 1' }, user.id);
  const type2 = community.resolveCondition({ conditionName: 'Test syndrome type 2' }, user.id);
  assert.notEqual(type1?.id, type2?.id);
  assert.equal(community.findCondition(first.diseaseId!)?.curated, false);
});

test('related-condition saves create an empty community without sharing private records or enrolling the author', () => {
  const owner = store.createGuest('patient');
  const record = community.saveRecordWithCondition(owner.id, { kind: 'note', title: 'Private symptom note', content: 'Private personal detail', conditionName: 'A distinct private condition' });
  const viewer = store.createGuest('researcher');
  const data = community.communityData(viewer.id, record.diseaseId);
  assert.ok(data.condition);
  assert.equal(data.condition.memberCount, 0);
  assert.equal(data.condition.postCount, 0);
  assert.deepEqual(data.members, []);
  assert.deepEqual(data.posts, []);
  assert.deepEqual(data.projects, []);
  assert.deepEqual(community.getCommunityProfiles(owner.id), []);
  assert.equal(store.getRecord(record.id, viewer.id), null);
});

test('read-only and unauthorized edits cannot create ghost communities or alter the related condition', () => {
  const owner = store.createGuest('researcher'), recipient = store.createGuest('doctor'), outsider = store.createGuest('patient');
  const record = community.saveRecordWithCondition(owner.id, { kind: 'project', title: 'Protected study', diseaseId: 'pompe' });
  store.shareRecord(record.id, owner.id, recipient.email);
  const before = community.listConditions().length;
  for (const user of [recipient, outsider]) assert.throws(() => community.saveRecordWithCondition(user.id, { id: record.id, kind: 'project', title: 'Attempted edit', conditionName: 'Ghost syndrome' }), /read-only/);
  assert.equal(community.listConditions().length, before);
  assert.equal(store.getRecord(record.id, owner.id)?.diseaseId, 'pompe');
});

test('a failed record write rolls back its new condition and aliases', () => {
  const user = store.createGuest('researcher');
  const before = community.listConditions().length;
  store.db().exec("CREATE TEMP TRIGGER fail_record_save BEFORE INSERT ON records WHEN NEW.kind='note' BEGIN SELECT RAISE(ABORT,'Simulated write failure'); END");
  try { assert.throws(() => community.saveRecordWithCondition(user.id, { kind: 'note', title: 'Failure', conditionName: 'Rolled back syndrome' }), /Simulated write failure/); }
  finally { store.db().exec('DROP TRIGGER fail_record_save'); }
  assert.equal(community.listConditions().length, before);
  assert.equal(store.db().prepare('SELECT normalized_name FROM condition_aliases WHERE normalized_name=?').get('rolled back syndrome'), undefined);
});

test('users can join multiple communities independently, update their aliases, and leave only one', () => {
  const user = store.createGuest('patient');
  community.saveCommunityProfile(user.id, { alias: 'River', diseaseId: 'fabry', bio: 'Here to connect' });
  community.saveCommunityProfile(user.id, { alias: 'River', conditionName: 'Pompe disease', bio: 'Here too' });
  community.saveCommunityProfile(user.id, { alias: 'New alias', diseaseId: 'fabry', bio: 'Updated' });
  const directory = community.communityData(user.id);
  assert.equal(directory.condition, null);
  assert.equal(directory.profile, null);
  assert.equal(directory.profiles.length, 2);
  assert.equal(directory.conditions.find(item => item.id === 'fabry')?.joined, true);
  assert.equal(community.communityData(user.id, 'fabry').profile?.alias, 'New alias');
  community.leaveCommunity(user.id, 'fabry');
  assert.equal(community.getCommunityProfile(user.id, 'fabry'), null);
  assert.ok(community.getCommunityProfile(user.id, 'pompe'));
});

test('posts are condition-scoped, opt-in only, and deletion is restricted to their author', () => {
  const owner = store.createGuest('patient'), other = store.createGuest('patient');
  assert.throws(() => community.addCommunityPost(owner.id, 'rett', 'Before joining'), /Join this community/);
  community.saveCommunityProfile(owner.id, { alias: 'Author', diseaseId: 'rett', bio: '' });
  community.saveCommunityProfile(other.id, { alias: 'Other member', diseaseId: 'pompe', bio: '' });
  const post = community.addCommunityPost(owner.id, 'rett', 'An experience I choose to share');
  assert.throws(() => community.addCommunityPost(other.id, 'rett', 'Cross-community post'), /Join this community/);
  assert.equal(community.communityData(owner.id, 'rett').posts[0].own, true);
  assert.equal(community.communityData(other.id, 'rett').posts[0].own, false);
  assert.deepEqual(community.communityData(other.id, 'pompe').posts, []);
  assert.deepEqual(community.communityData(undefined, 'rett').posts, []);
  assert.deepEqual(community.communityData(undefined, 'rett').members, []);
  assert.deepEqual(community.communityData(undefined).profiles, []);
  assert.throws(() => community.deleteCommunityPost(other.id, post.id), /Post not found/);
  assert.equal(community.communityData(owner.id, 'rett').posts.length, 1);
  community.leaveCommunity(owner.id, 'rett');
  assert.throws(() => community.addCommunityPost(owner.id, 'rett', 'After leaving'), /Join this community/);
  community.deleteCommunityPost(owner.id, post.id);
  assert.deepEqual(community.communityData(other.id, 'rett').posts, []);
});

test('a new-name community join resolves canonically and shared research stays in its condition', () => {
  const researcher = store.createGuest('researcher');
  const joined = community.saveCommunityProfile(researcher.id, { alias: 'Research friend', conditionName: 'Another rare condition', bio: '' });
  const record = community.saveRecordWithCondition(researcher.id, { kind: 'project', title: 'Published related study', conditionName: 'Another-rare-condition', content: 'Explicitly public' });
  assert.equal(record.diseaseId, joined.condition.id);
  assert.equal(community.communityData(researcher.id, record.diseaseId).projects.length, 0);
  store.publishRecord(record.id, researcher.id, true);
  assert.equal(community.communityData(researcher.id, record.diseaseId).projects[0].id, record.id);
  assert.equal(community.communityData(researcher.id, 'fabry').projects.length, 0);
  const privateProject = community.saveRecordWithCondition(researcher.id, { kind: 'project', title: 'Unpublished related study', diseaseId: record.diseaseId });
  assert.ok(!community.communityData(researcher.id, record.diseaseId).projects.some(item => item.id === privateProject.id));
  // Other communities' newer projects must not crowd this condition out of a global 50-item limit.
  for (let index = 0; index < 51; index++) {
    const newer = community.saveRecordWithCondition(researcher.id, { kind: 'project', title: `Other study ${index}`, diseaseId: 'fabry' });
    store.publishRecord(newer.id, researcher.id, true);
  }
  assert.ok(community.communityData(researcher.id, record.diseaseId).projects.some(item => item.id === record.id));
});

test('unknown IDs and invalid custom names are rejected without creating entries', () => {
  const user = store.createGuest('patient');
  const before = community.listConditions().length;
  assert.throws(() => community.resolveCondition({ diseaseId: 'not-a-condition' }, user.id), /Choose an existing/);
  for (const conditionName of ['!', '   ', 'x'.repeat(121)]) {
    if (conditionName.trim()) assert.throws(() => community.resolveCondition({ conditionName }, user.id));
  }
  assert.throws(() => community.resolveCondition({ diseaseId: 'fabry', conditionName: 'Rett syndrome' }, user.id), /Choose one/);
  assert.throws(() => community.communityData(user.id, 'unknown'), /Community not found/);
  assert.equal(community.listConditions().length, before);
});
