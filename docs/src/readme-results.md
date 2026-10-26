## Results

We ran the live engine on OpenCV's Plaza sample clip, which we labelled by eye ourselves, at {{say:2 analysed frames a
second}}. We also tested the plate reader on real photos and the camera pool on our x86 dev box. Each number links to
its file in [eval/results](eval/results). The Graviton and COOL numbers come with the AWS run.

| Plaza clip, {{say:2 fps}} | Labelled | Matched | False alarms | Missed | Precision | Recall |
| --- | --- | --- | --- | --- | --- | --- |
| Enters the left walkway | {{eval.plaza.fps_2.enter.truth}} | {{eval.plaza.fps_2.enter.tp}} | {{eval.plaza.fps_2.enter.fp}} | {{eval.plaza.fps_2.enter.fn}} | {{eval.plaza.fps_2.enter.precision}} | {{eval.plaza.fps_2.enter.recall}} |
| Crosses the edge going in | {{eval.plaza.fps_2.cross_in.truth}} | {{eval.plaza.fps_2.cross_in.tp}} | {{eval.plaza.fps_2.cross_in.fp}} | {{eval.plaza.fps_2.cross_in.fn}} | {{eval.plaza.fps_2.cross_in.precision}} | {{eval.plaza.fps_2.cross_in.recall}} |
| Crosses the edge going out | {{eval.plaza.fps_2.cross_out.truth}} | {{eval.plaza.fps_2.cross_out.tp}} | {{eval.plaza.fps_2.cross_out.fp}} | {{eval.plaza.fps_2.cross_out.fn}} | {{eval.plaza.fps_2.cross_out.precision}} | {{eval.plaza.fps_2.cross_out.recall}} |

| Measure | Result |
| --- | --- |
| Every watch together at {{say:2 fps}}, precision and recall | {{eval.plaza.fps_2.all.precision}} ({{eval.plaza.fps_2.all.precision_ci}}), {{eval.plaza.fps_2.all.recall}} ({{eval.plaza.fps_2.all.recall_ci}}) |
| Line crossings going in at {{say:1 fps}}, recall before and after the tracker fix | {{eval.plaza_before_fix.fps_1.cross_in.recall}}, {{eval.plaza.fps_1.cross_in.recall}} |
| Plates read exactly, of real photos the grammar covers (not gate CCTV) | {{plates.commons.exact}} of {{plates.commons.plates}}; wrong {{plates.commons.wrong}}, unread {{plates.commons.unread}} |
| 8 busy cameras on 6 CPUs of our 2013 dev box: analysed frames a second per camera, without and with a watch described by looks | {{bench.devbox_6cpu_no_text.fps_per_camera|bare}}, {{bench.devbox_6cpu_text.fps_per_camera|bare}} |
| Memory of the whole app in those runs, median | {{bench.devbox_6cpu_no_text.memory_total}}, {{bench.devbox_6cpu_text.memory_total}} |

What goes wrong, so far: people walking close together, seen from above, merge into one box and one of them is
missed; at very low frame rates two similar people can swap ids; a plate box that cuts characters can give a wrong but
valid read; the age guard also refuses some harmless phrases, such as "a kids bicycle left on the ramp"; and a watch
described by looks costs the most on old CPUs, so on our dev box, which has no AVX2, it stays under one analysed frame
a second per camera. The first
false alarm and first miss of each watch are saved as pictures in
[eval/results/failures](eval/results/failures). One labelled clip is a small sample of one scene, and we have no
society footage labelled yet. The [report](docs/report.md) has the details.
