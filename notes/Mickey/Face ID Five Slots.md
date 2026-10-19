# Face ID Five Slots

Face ID of five people stays on the coprocessor. Embeddings live in that chip's NVS. The main board stores only slot-to-name in its own NVS. Recognition does not need the cloud.

Enroll sends a slot number. The coprocessor captures several frames, builds an embedding, and acknowledges. Forget clears a slot. Unknown faces get the generic enter reaction. Photos upload only when someone asks. The camera is not streamed. See [[Vision Reflex Table]] and [[Vision Coprocessor Decision]].
