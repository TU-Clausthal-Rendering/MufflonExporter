bl_info = {
    "name": "Align to Face Normals",
    "description": "Replaces vertex normals of selected faces with the face normals.",
    "author": "Johannes Jendersie",
    "version": (1, 0),
    "blender": (2, 78, 0),
    "location": "3D View > Quick Search",
    "category": "Mesh",
    "support": "COMMUNITY"
}

import bpy, bmesh, array
from mathutils import Vector


class FaceNormalsCalculator(bpy.types.Operator):
    """Replaces vertex normals of selected faces with the face normals."""
    bl_idname = "mesh.calculate_face_normals"
    bl_label = "Align to Face Normals"
    bl_options = set()

    @classmethod
    def poll(cls, context):
        return context.object and context.mode == "EDIT_MESH" and context.object.data

    # Remove values close to zero
    @staticmethod
    def clean_normal(normal):
        cleanN = (0 if abs(normal[0]) < 1e-5 else normal[0],
                  0 if abs(normal[1]) < 1e-5 else normal[1],
                  0 if abs(normal[2]) < 1e-5 else normal[2])
        return Vector(cleanN).normalized()

    # Calculate a vertex normal from the two adjacent edges.
    @staticmethod
    def calc_normal(verts, localIdx):
        n = len(verts)
        v0 = verts[localIdx].co
        v1 = verts[(localIdx + 1) % n].co
        v2 = verts[(localIdx - 1) % n].co
        t01 = v1 - v0
        t02 = v2 - v0
        normal = t01.cross(t02)
        normal = FaceNormalsCalculator.clean_normal(normal)
        return normal.normalized()

    def execute(self, context):
        context.object.update_from_editmode()
        #selected_faces = [f for f in context.object.data.polygons if f.select]

        mesh = context.object.data

        # Get the old normals, such that we only change those of the selected faces
        new_normals = [Vector((0,0,0))] * len(mesh.loops) # loops = half_edges
        if mesh.has_custom_normals:
            mesh.calc_normals_split() # Get the custom normals into the mesh.loops
            for i,l in enumerate(mesh.loops):
                new_normals[i] = FaceNormalsCalculator.clean_normal(l.normal)

        # Replace normals for all selected faces.
        # If face share a vertex, the last one wins the race.
        bm = bmesh.from_edit_mesh(mesh)
        for f in bm.faces:
            if f.select:
                # Compute new normals for each vertex
                for i,v in enumerate(f.verts):
                    normal = FaceNormalsCalculator.calc_normal(f.verts, i)
                    for l in v.link_loops:
                       # print(l.calc_normal())
                        new_normals[l.index] = normal

        # Replace the old normals
        bpy.ops.object.mode_set(mode="OBJECT")
        mesh.use_auto_smooth = True
        mesh.normals_split_custom_set(new_normals)
        mesh.free_normals_split()
        bpy.ops.object.mode_set(mode="EDIT")

        #bmesh.update_edit_mesh(mesh, False, False)
        return {'FINISHED'}


def register():
    bpy.utils.register_class(FaceNormalsCalculator)


def unregister():
    bpy.utils.unregister_class(FaceNormalsCalculator)


if __name__ == '__main__':
    register()
