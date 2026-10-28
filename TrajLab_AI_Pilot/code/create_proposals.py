"""Encode manually selected image-space waypoints. No fitting to human coordinates."""
import argparse, json, math, hashlib
from pathlib import Path
import numpy as np
from scipy.interpolate import PchipInterpolator

parser=argparse.ArgumentParser()
parser.add_argument('--source',type=Path,required=True,help='Directory containing the extracted original dataset')
parser.add_argument('--root',type=Path,default=Path(__file__).resolve().parents[1])
args=parser.parse_args()
SOURCE=args.source
OUT=args.root
scenes=[]
for index,path in enumerate(sorted(SOURCE.glob('images/*/paths.json')),1):
    scene=json.loads(path.read_text())
    scene['index']=index
    scene['folder']=path.parent.name
    scenes.append(scene)
if len(scenes)!=39: raise ValueError('This frozen pilot expects the original 39-scene export')
(OUT/'data').mkdir(parents=True,exist_ok=True)
# Internal waypoints only; exact current scene start and goal are appended automatically.
# These were selected after visually inspecting each original and its human overlay.
PLAN={
1: ([(.485,.80),(.475,.62),(.460,.44)], ['personal_space'], 'medium', 'Use the central paved opening, between the nearer pedestrians rather than cutting toward either side.', 'Distant pedestrians partly obscure the approach to the goal; yield before the gap closes.'),
2: ([(.530,.80),(.540,.64),(.537,.48)], ['personal_space','group'], 'low', 'Stay on the sidewalk, using the visible opening between the two approaching people rather than the road or tree bed.', 'The people may be walking together. This gap requires a clearance and timing check; yielding is preferable if it narrows.'),
3: ([(.465,.80),(.445,.60),(.450,.43)], [], 'medium', 'Keep inside the sidewalk with a modest buffer from the curb and vehicles.', 'Wall clearance and vehicle motion cannot be verified from this frame.'),
4: (None, ['delay'], 'low', 'Wait: the marked goal lies within or behind an occupied group, with no clearly visible approach to it that avoids that group.', 'Confirm a ground-plane goal and ask the group to clear a passage; do not treat the human image curves as collision-free.'),
5: ([(.475,.82),(.445,.68),(.435,.57)], ['traffic'], 'low', 'Take the visible opening to the left of the central group, avoiding the much longer sweep through their space on the right.', 'The route enters a roadway or event crossing area. Verify traffic control and permission before movement.'),
6: ([(.555,.83),(.585,.73),(.585,.63)], ['personal_space'], 'low', 'Follow the paved central-right corridor rather than approaching the nearer pair on the left.', 'The final approach is occupied or partly occluded; stop short until the person ahead moves.'),
7: ([(.580,.82),(.615,.68),(.610,.58)], ['personal_space'], 'low', 'Pass to the right of the event cart and staff, remaining on the path instead of making the left grass detour.', 'Check clearance between the cart and the nearer person on the right; the final goal region is partly occluded.'),
8: ([(.485,.81),(.475,.67),(.470,.58)], ['personal_space'], 'medium', 'Remain near the middle of the concrete sidewalk rather than stepping onto the grass or the roadside strip.', 'Yield to the approaching pedestrians in the distant narrowing.'),
9: ([(.380,.82),(.280,.65),(.240,.49),(.275,.38)], ['personal_space','detour'], 'low', 'Use the left-side opening around the foreground people instead of a direct line through the central pair.', 'The last approach requires the far pair to leave a gap. The left crossing pedestrian also requires timing.'),
10: ([(.435,.85),(.365,.74),(.290,.65)], ['personal_space'], 'low', 'Follow the open aisle on the left of the people standing along the right wall; do not cut into that line.', 'The left foreground pedestrian can close the aisle; pause if that happens.'),
11: ([(.390,.88),(.330,.80),(.305,.69)], ['personal_space'], 'low', 'Curve left of the stationary conversation group while staying on the established walking surface.', 'The remaining opening is narrow; projected spacing is not a physical clearance measurement.'),
12: ([(.365,.87),(.280,.78),(.260,.68)], ['personal_space'], 'low', 'Go around the left edge of the central standing group rather than cutting between its members.', 'Yield to the approaching person on the left before entering the final gap.'),
13: ([(.490,.84),(.510,.76)], ['terrain','personal_space'], 'low', 'Exit the grass through the visible gap ahead while avoiding the very close person on the right.', 'The start is already on grass. Traversing it here is not evidence of a voluntary lawn shortcut; confirm the goal location.'),
14: ([(.420,.87),(.385,.78),(.415,.70)], ['personal_space'], 'low', 'Curve to the left of the stationary central group rather than cutting through it.', 'The nearby moving person on the left must clear the gap; stop rather than squeeze past.'),
15: ([(.490,.83),(.477,.70),(.462,.60)], [], 'medium', 'Use the open paved route, keeping off the adjacent lawn.', 'No obvious immediate trade-off is visible. The distant crowd still requires normal yielding.'),
16: (None, ['delay'], 'low', 'Wait: the wall-side space is separated by a visible exhibit barrier and the other approach is occupied by a dense group.', 'Do not cross the exhibit barrier merely because that route is short. Seek a permitted opening or a clearer view.'),
17: (None, ['delay'], 'medium', 'Wait: a low bunting barrier spans the apparent direct route and people occupy the foreground passage.', 'A route around the barrier may exist outside the image; do not invent it or cross the barrier.'),
18: ([(.575,.78),(.635,.58),(.655,.43),(.620,.35)], ['personal_space'], 'low', 'Approach around the right outside edge of the central group, leaving their interaction space intact.', 'Avoid the event equipment and yield before rejoining near the goal.'),
19: ([(.480,.80),(.455,.62),(.450,.45)], ['personal_space'], 'medium', 'Use the relatively direct opening to the left of the central person, avoiding the activity and furniture on the right.', 'The final opening depends on pedestrian motion; no timing is encoded.'),
20: ([(.450,.80),(.410,.65),(.390,.52)], ['personal_space'], 'medium', 'Use the open central-left strip without sweeping through the oncoming pedestrians on either side.', 'The oncoming person near the goal may require a pause.'),
21: (None, ['delay'], 'low', 'Wait for the crossing foreground pedestrian and the oncoming pair to clear before exiting the grass onto the narrow path.', 'An immediate left turn would squeeze between moving people. No off-frame bypass is assumed.'),
22: ([(.445,.83),(.405,.71),(.390,.60)], ['personal_space','terrain'], 'low', 'Follow the path to the left of the approaching group, avoiding a cut through its center.', 'The goal appears at the path/grass boundary. Verify it is a reachable ground point rather than assuming grass access.'),
23: (None, ['delay'], 'low', 'Wait for the crossing pedestrian to clear the leftward exit rather than drawing a line through the occupied foreground.', 'The goal and part of the left exit are occluded; verify a ground-plane continuation before moving.'),
24: ([(.450,.85),(.425,.74),(.437,.64)], ['personal_space'], 'low', 'Follow behind the walkers on the left side of the bins instead of overtaking through their group.', 'This is a following corridor, not permission to pass between the walkers; progress depends on their motion.'),
25: ([(.350,.88),(.210,.79),(.165,.70),(.200,.59)], ['terrain','personal_space','detour'], 'low', 'Go around the left outside of the walking group rather than directly through its members.', 'The outer arc and goal are close to grass; confirm access and curb geometry. Yield to the turning pedestrian.'),
26: (None, ['delay'], 'low', 'Wait for the pair crossing the foreground and the occupied goal area to clear rather than committing to either detour now.', 'Both drawn human detours depend on pedestrian timing. Replan from a short clip or after a brief pause.'),
27: ([(.450,.85),(.425,.75),(.433,.67)], ['personal_space'], 'low', 'Use the small opening left of the central approaching person and stay away from the stair/railing edge.', 'The goal approach is partly occupied. Pause before entering if the opening closes.'),
28: (None, ['delay'], 'medium', 'Wait: people occupy the immediate landing and the apparent direct path intersects the foreground person.', 'Do not squeeze between legs or use an unseen stair-edge bypass. Replan when the landing clears.'),
29: ([(.490,.82),(.475,.67),(.463,.55)], ['personal_space'], 'medium', 'Approach along the visible open pavement, without crossing the event barriers or the group on the right.', 'The final crowd gap requires a fresh observation before entry.'),
30: ([(.550,.83),(.570,.71),(.560,.61),(.540,.54)], ['personal_space'], 'low', 'Keep in the open center of the road-like event aisle, left of the dog and nearer people on the right.', 'Verify that the event aisle is open to robots; yield before entering the distant pedestrian gap.'),
31: ([(.485,.83),(.475,.71),(.470,.61)], ['personal_space'], 'low', 'Use the central opening rather than a broad rightward sweep into the approaching pedestrians.', 'The goal is between moving people; approach slowly and wait before the last gap if needed.'),
32: ([(.405,.83),(.325,.72),(.285,.62)], ['traffic'], 'low', 'Approach the goal to the left of the van, avoiding the narrow sidewalk crowd on its right.', 'This crosses into the road-like surface. Vehicle motion and event traffic permission must be checked before any execution.'),
33: ([(.555,.82),(.580,.69),(.590,.57)], ['personal_space'], 'low', 'Join and follow the sidewalk rather than sweeping left toward the nearby vehicles.', 'The marked start is near a curb and the goal approach is crowded; verify curb traversal and yield.'),
34: ([(.555,.83),(.585,.70),(.590,.59)], ['personal_space'], 'low', 'Use the paved opening between the foreground person on the right and the group ahead, without a lawn shortcut.', 'The approaching group can close the corridor; do not use the lawn as an automatic escape.'),
35: (None, ['delay'], 'low', 'Wait for the people ahead to advance instead of squeezing through the narrow gap between them and the equipment.', 'The goal is beyond an occupied bottleneck and cannot be certified reachable from this single frame.'),
36: ([(.490,.83),(.480,.70),(.480,.61)], ['personal_space'], 'medium', 'Stay in the corridor to the right of the column and outside the dining area.', 'Check the final passing width and yield to the person at the end of the corridor.'),
37: ([(.500,.84),(.485,.72),(.465,.60)], ['personal_space'], 'low', 'Follow the middle of the sidewalk, leaving space from the family on the left and the crossing adults on the right.', 'Pedestrian motion is uncertain; do not accelerate through the opening.'),
38: ([(.480,.82),(.485,.70),(.530,.61),(.600,.565)], ['terrain','personal_space'], 'low', 'Exit the grass through the center-left opening before turning right toward the goal, avoiding the nearer group on the right.', 'The start is already on grass. Check that the final turn does not enter the right-hand group and verify the goal.'),
39: ([(.445,.83),(.405,.70),(.395,.60)], ['personal_space'], 'low', 'Use the left side of the sidewalk to approach the group without stepping onto the lawn or the roadside strip.', 'The final gap is small and partly occupied; pause for a clearer approach.'),
}

def curve_from_waypoints(points):
    """Shape-preserving x(y), represented as exact cubic Bezier segments.
    y is monotone for these image-space paths. Unlike an unconstrained spline,
    PCHIP does not overshoot local x extrema between consecutive waypoints.
    """
    p=np.array(points,float)
    t=1-p[:,1]
    if not np.all(np.diff(t)>0): raise ValueError('Expected forward/monotone image-y')
    f=PchipInterpolator(t,p[:,0]);der=f.derivative()
    segments=[]
    for i in range(len(p)-1):
        dt=t[i+1]-t[i]
        segments.append({'type':'curve','c1':[float(p[i,0]+der(t[i])*dt/3),float(p[i,1]-dt/3)],'c2':[float(p[i+1,0]-der(t[i+1])*dt/3),float(p[i+1,1]+dt/3)]})
    return segments

records=[]
for s in scenes:
    i=s['index'];im=s['image'];way,norms,conf,rationale,caveat=PLAN[i]
    points=[] if way is None else [im['start']]+[list(x) for x in way]+[im['goal']]
    rec={'proposal_id':f'AI_PILOT_{i:02d}','image_id':im['id'],'index':i,'folder':s['folder'],'image_title':im['title'],'source_name':im['source_name'],'width':im['width'],'height':im['height'],'start':im['start'],'goal':im['goal'],'decision':'wait' if way is None else 'path','style':'curve','points':points,'segments':[] if way is None else curve_from_waypoints(points),'norms':norms,'confidence_label':conf,'confidence_type':'uncalibrated qualitative assessment, not a probability','rationale':rationale,'required_review':caveat,'physically_validated':False,'human_references_seen':True,'model':'GPT-6 Astra Pro','generation_mode':'interactive visual review plus manually specified waypoints; deterministic PCHIP interpolation; no fine-tuning or standalone VLM API run'}
    records.append(rec)
obj={'format':'trajlab-ai-pilot-v1','source_export':'trajlab-export-v2, 2026-10-15T20:52:24+00:00','coordinate_system':'x/width, y/height; origin top-left','scope':'Reference-conditioned exploratory proposals. Not held-out predictions and not additional human responses. No robot-safety certification. Wait has no movement curve. This is an interchange file, not a verified TrajLab import schema.','proposals':records}
p=OUT/'data/ai_proposals.json';p.write_text(json.dumps(obj,indent=2))
print('Proposals:',len(records),'paths:',sum(r['decision']=='path' for r in records),'waits:',sum(r['decision']=='wait' for r in records))
print('SHA256',hashlib.sha256(p.read_bytes()).hexdigest())
# Share only the path-level pseudonymous reference data needed for reproducibility.
raw=json.loads((SOURCE/'dataset.json').read_text())
keep=['id','image_id','image_title','code','role','decision','style','points','segments','curve_64','norms','confidence','duration_ms','n_edits','route','outlier','trimmed','excluded']
references={'source_export':raw['exported_at'],'settings':{k:raw['settings'][k] for k in ['cluster_threshold','ambiguity_threshold','min_mode_share','exclude_outliers','compiled_method','norms']},'images':[{'index':s['index'],'folder':s['folder'],**s['image'],'summary':s['summary'],'routes':s['routes'],'compiled':s['compiled']} for s in scenes],'annotations':[{k:a[k] for k in keep if k in a} for a in raw['annotations']],'participant_account_count':len(raw['participants']),'vlm_image_count':len(raw['vlm_images']),'vlm_rating_count':len(raw['vlm_ratings']),'candidate_count':len(raw['_raw']['candidates']),'warning':'Participant names, emails, free-text comments, and account records are not included. Participant codes are pseudonyms. Photographs in derivative images have not been face-deidentified.'}
(OUT/'data/human_reference.json').write_text(json.dumps(references,indent=2))
