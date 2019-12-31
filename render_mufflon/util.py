import bpy
import mathutils
from .bindings import Vec3

# Blender has: up = z, but target is: up = y
def flip_space(vec):
    return [vec[0], vec[2], -vec[1]]
def flip_space_mat(mat):
    return mathutils.Matrix([mat[0], mat[2], -mat[1], mat[3]])
    
def to_vec3(vec):
    return Vec3(vec[0], vec[1], vec[2])
    
def mat4x4_to_cfloat_array(mat):
    return [ mat[0][0], mat[0][1], mat[0][2], mat[0][3],
            mat[1][0], mat[1][1], mat[1][2], mat[1][3],
            mat[2][0], mat[2][1], mat[2][2], mat[2][3] ]
			
def property_array_to_color(prop_array):
    return [ prop_array[0], prop_array[1], prop_array[2] ]

def get_image_input(material, to_node, from_node, targetInputName, isScalar, bakeTextures):
    if from_node.bl_idname == 'ShaderNodeTexImage':
        return bpy.path.abspath(from_node.image.filepath)
    else:
        # Find out what socket we take things from
        fromSocketName = to_node.inputs[targetInputName].links[0].from_socket.name
        return bake_texture_node(from_node, material, fromSocketName, isScalar, bakeTextures)

def get_color_input(material, node, inputName, bakeTextures):
    input = node.inputs[inputName]
    if len(input.links) == 0:
        return property_array_to_color(input.default_value)
    else:
        return get_image_input(material, node, input.links[0].from_node, inputName, False, bakeTextures)

def get_scalar_input(material, node, inputName, bakeTextures):
    input = node.inputs[inputName]
    if len(input.links) == 0:
        return input.default_value
    else:
        # TODO: support for converters!
        return get_image_input(material, node, input.links[0].from_node, inputName, True, bakeTextures)

def get_scalar_def_only_input(node, inputName):
    input = node.inputs[inputName]
    if len(input.links) == 0:
        return input.default_value
    else:
        raise Exception("non-value where only value is expected (node '%s.%s')"%(node.name, inputName))