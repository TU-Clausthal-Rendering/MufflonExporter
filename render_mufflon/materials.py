import bpy
import math
import mathutils
import ctypes
from ctypes import *
from enum import Enum
from . import (bindings, util)
from .bindings import *
from .lights import (EmissionType, get_emission_type)
from .util import *


def find_material_output_node(material):
    if not (hasattr(material, 'node_tree') or hasattr(material.node_tree, 'nodes')):
        raise Exception("%s is not a node-based material"%(material.name))
    for node in material.node_tree.nodes:
        if node.bl_idname == 'ShaderNodeOutputMaterial' and node.is_active_output:
            return node
    return None
    
def get_microfacet_distribution(node):
    if node.distribution == 'SHARP':
        # Doesn't matter since there's gonna be roughness 0
        return NormalDistFunction.GGX
    elif node.distribution == 'BECKMANN':
        return NormalDistFunction.BECKMANN
    elif node.distribution == 'MULTI_GGX':
        # TODO: uhh do we have a parameter for this?
        return NormalDistFunction.GGX
    elif node.distribution != 'GGX':
        # Fallback
        return NormalDistFunction.GGX
    return NormalDistFunction.GGX
    
def add_texture_value_r32(interface, value):
    val = ctypes.c_float(value)
    hdl = interface.world_add_texture_value(byref(val), 1, TextureSampling.NEAREST)
    if hdl == c_void_p(0):
        raise Exception("Failed to add R32 value texture")
    return hdl
    
def add_texture_value_rgba32(interface, values):
    value_type = ctypes.c_float * 4
    hdl = interface.world_add_texture_value(value_type(*values), 4, TextureSampling.NEAREST)
    if hdl == c_void_p(0):
        raise Exception("Failed to add RGBA32 value texture")
    return hdl

def add_texture_r32(interface, valOrString):
    if isinstance(valOrString, str):
        hdl = interface.world_add_texture(valOrString, TextureSampling.LINEAR, MipmapType.NONE)
        if hdl == c_void_p(0):
            raise Exception("Failed to add R32 texture")
        return hdl
    else:
        return add_texture_value_r32(interface, valOrString)
def add_texture_rgba32(interface, valOrString):
    if isinstance(valOrString, str):
        hdl = interface.world_add_texture(valOrString, TextureSampling.LINEAR, MipmapType.NONE)
        if hdl == c_void_p(0):
            raise Exception("Failed to add RGBA32 texture")
        return hdl
    else:
        return add_texture_value_rgba32(interface, valOrString)

# Transform an RGB [0,1]^3 color attribute into a physical absorption value [0,∞]
def get_mapped_absorption(color):
    return [ max(0, 1 / (1e-10 + color[0]) - 1), max(0, 1 / (1e-10 + color[1]) - 1), max(0, 1 / (1e-10 + color[2]) - 1) ]

def write_walter_node(interface, material, node):
    matParams = MaterialParams(Medium(Vec2(1.0, 1.0), Vec3(0.0, 0.0, 0.0)),
                               MaterialParamType.WALTER, c_void_p(0),
                               MaterialParamsDisplacement(c_void_p(0), c_void_p(0), 0.0, 0.0))
    if node.distribution == 'SHARP':
        roughness = add_texture_value_r32(interface, 0.0)
    else:
        roughness = add_texture_r32(interface, get_scalar_input(material, node, 'Roughness', False))
    ndf = get_microfacet_distribution(node)
    absorption = get_scalar_def_only_input(node, 'Color')
    if (absorption is not None) and not isinstance(absorption, str):
        absorption = get_mapped_absorption(absorption)
    absorption = to_vec3(absorption)
    ior = get_scalar_def_only_input(node, 'IOR')
    
    matParams.inner.walter = WalterParams(roughness, c_uint32(ShadowingModel.VCAVITY),
                                          c_uint32(ndf), absorption, ior)
    return matParams

def write_emissive_node(interface, material, node):
    matParams = MaterialParams(Medium(Vec2(1.0, 1.0), Vec3(0.0, 0.0, 0.0)),
                               MaterialParamType.EMISSIVE, c_void_p(0),
                               MaterialParamsDisplacement(c_void_p(0), c_void_p(0), 0.0, 0.0))
    # Check if the color input specifies a color temperature
    emissionType = get_emission_type(node)
    if emissionType == EmissionType.BLACKBODY:
        colorNode = node.inputs['Color'].links[0].from_node
        # TODO: convert temperature
        radiance = get_scalar_def_only_input(colorNode, 'Temperature')
    elif emissionType == EmissionType.GONIOMETRIC:
        raise Exception("material '%s' has goniometric emission type, which is not supported!"%(material.name))
    else:
        radiance = get_color_input(material, node, 'Color', False)
    scale = get_scalar_def_only_input(node, 'Strength')
    radiance = add_texture_value_rgba32(interface, radiance)
    matParams.inner.emissive = EmissiveParams(radiance, Vec3(scale, scale, scale))
    return matParams

def write_diffuse_node(interface, material, node):
    matParams = MaterialParams(Medium(Vec2(1.0, 1.0), Vec3(0.0, 0.0, 0.0)),
                               MaterialParamType.LAMBERT, c_void_p(0),
                               MaterialParamsDisplacement(c_void_p(0), c_void_p(0), 0.0, 0.0))

    if len(node.inputs['Roughness'].links) == 0:
        # Differentiate between Lambert and Oren-Nayar
        albedo = add_texture_rgba32(interface, get_color_input(material, node, 'Color', False))
        if node.inputs['Roughness'].default_value == 0.0:
            matParams.inner.lambert = LambertParams(albedo)
        else:
            roughness = get_scalar_def_only_input(node, 'Roughness')
            matParams.innerType = MaterialParamType.ORENNAYAR
            matParams.inner.orennayar = OrennayarParams(albedo, roughness)
        return matParams
    else:
        raise Exception("non-value for diffuse roughness (node '%s')"%(node.name))

def write_torrance_node(interface, material, node):
    matParams = MaterialParams(Medium(Vec2(1.0, 1.0), Vec3(0.0, 0.0, 0.0)),
                               MaterialParamType.TORRANCE, c_void_p(0),
                               MaterialParamsDisplacement(c_void_p(0), c_void_p(0), 0.0, 0.0))
                               
    albedo = add_texture_rgba32(interface, get_color_input(material, node, 'Color', False))
    if node.distribution == 'SHARP':
        roughness = add_texture_value_r32(interface, 0.0)
    else:
        roughness = get_scalar_input(material, node, 'Roughness', False)
        if not isinstance(roughness, str):
            roughness *= roughness
        roughness = add_texture_r32(interface, roughness)
    ndf = get_microfacet_distribution(node)
    # TODO: check for anisotropy
    matParams.inner.torrance = TorranceParams(roughness, c_uint32(ShadowingModel.VCAVITY), ndf, albedo)
    return matParams
        
def write_nonrecursive_node(interface, material, node):
    # TODO: support for principled BSDF?
    if node.bl_idname == 'ShaderNodeBsdfDiffuse':
        return write_diffuse_node(interface, material, node)
    elif node.bl_idname == 'ShaderNodeBsdfGlossy' or node.bl_idname == 'ShaderNodeBsdfAnisotropic':
        return write_torrance_node(interface, material, node)
    elif node.bl_idname == 'ShaderNodeBsdfGlass' or node.bl_idname == 'ShaderNodeBsdfRefraction':
        return write_walter_node(interface, material, node)
    elif node.bl_idname == 'ShaderNodeEmission':
        return write_emissive_node(interface, material, node)
    else:
        # TODO: allow recursion? Currently not supported by our renderer
        raise Exception("invalid mix-shader input (node '%s')"%(node.name))
    
def write_glass_node(interface, material, node):
    # First check if the color has texture input, in which case we can't use the full microfacet model
    if len(node.inputs['Color'].links) > 0:
        raise Exception("glass cannot have non-value color since absorption must not be a texture (node '%s')"%(node.name))
    else:
        matParams = write_walter_node(interface, material, node)
        matParams.innerType = MaterialParamType.MICROFACET
        return matParams

def write_mix_node(interface, material, node, hasAlphaAlready):
    matParams = MaterialParams(Medium(Vec2(1.0, 1.0), Vec3(0.0, 0.0, 0.0)),
                               MaterialParamType.BLEND, c_void_p(0),
                               MaterialParamsDisplacement(c_void_p(0), c_void_p(0), 0.0, 0.0))

    if len(node.inputs[1].links) == 0 or len(node.inputs[2].links) == 0:
        raise Exception("missing mixed-shader input (node '%s')"%(node.name))
    nodeA = node.inputs[1].links[0].from_node
    nodeB = node.inputs[2].links[0].from_node
    # First check if it's blend or fresnel
    if len(node.inputs['Fac'].links) == 0 or node.inputs['Fac'].links[0].from_node.bl_idname == 'ShaderNodeValue':
        # Blend
        layerA = write_nonrecursive_node(interface, material, nodeA)
        layerB = write_nonrecursive_node(interface, material, nodeB)
        matParams.inner.blend = BlendParams(BlendLayer(1.0),
                                            BlendLayer(1.0))
        matParams.inner.blend.a.mat = POINTER(MaterialParams)(layerA)
        matParams.inner.blend.b.mat = POINTER(MaterialParams)(layerB)
        # TODO: Check validity
        # Emissive materials don't get blended, they're simply both
        if (nodeA.bl_idname == 'ShaderNodeBsdfDiffuse' and nodeB.bl_idname == 'ShaderNodeEmission') or (nodeB.bl_idname == 'ShaderNodeBsdfDiffuse' and nodeA.bl_idname == 'ShaderNodeEmission'):
            matParams.inner.blend.b.factor = 1.0
            matParams.inner.blend.a.factor = 1.0
        elif len(node.inputs['Fac'].links) > 0 and node.inputs['Fac'].links[0].from_node.bl_idname == 'ShaderNodeValue':
            matParams.inner.blend.b.factor = node.inputs['Fac'].links[0].from_node.outputs[0].default_value
            matParams.inner.blend.a.factor = 1 - matParams.inner.blend.b.factor
        else:
            matParams.inner.blend.b.factor = node.inputs['Fac'].default_value
            matParams.inner.blend.a.factor = 1 - matParams.inner.blend.b.factor
    elif node.inputs['Fac'].links[0].from_node.bl_idname == 'ShaderNodeFresnel' or node.inputs['Fac'].links[0].from_node.bl_idname == 'ShaderNodeLayerWeight':
        # Fresnel
        # Get the IOR
        # TODO: how to realize complex IOR with nodes? Arghhh
        # Layer weights are a bit weird: first off there are two possibilities (only one of which is valid), secondly
        # the actual (front-facing) IOR is defined as 1 / (1 - blend)
        facNode = node.inputs['Fac'].links[0].from_node
        if facNode.bl_idname == 'ShaderNodeLayerWeight':
            if node.inputs['Fac'].links[0].from_socket.identifier == 'Facing':
                raise Exception("cannot use 'Facing' output from layer weight for mixing shaders (node '%s')"%(node.name))
            fresnelIor = 1.0 / (1.0 - max(get_scalar_def_only_input(facNode, 'Blend'), 0.001))
        else:
            fresnelIor = get_scalar_def_only_input(facNode, 'IOR')
        
        # Check if we have a full microfacet model
        if (nodeA.bl_idname == 'ShaderNodeBsdfGlossy' or nodeA.bl_idname == 'ShaderNodeBsdfAnisotropic') and nodeB.bl_idname == 'ShaderNodeBsdfRefraction':
            # Check if the albedo is texturized (which doesn't work with the full microfacet model)
            if len(nodeA.inputs['Color'].links) > 0:
                matParams.innerType = MaterialParamType.FRESNEL
                ior = get_scalar_def_only_input(node.inputs['Fac'].links[0].from_node, 'IOR')
                refraction = write_nonrecursive_node(interface, material, nodeA)
                reflection = write_nonrecursive_node(interface, material, nodeB)
                matParams.inner.fresnel = FresnelParams(Vec2(ior, ior), POINTER(MaterialParams)(reflection),
                                                        POINTER(MaterialParams)(refraction))
            else:
                matParams = write_walter_node(interface, material, nodeB)
                matParams.innerType = MaterialParamType.MICROFACET
        else:
            matParams.innerType = MaterialParamType.FRESNEL
            ior = get_scalar_def_only_input(node.inputs['Fac'].links[0].from_node, 'IOR')
            refraction = write_nonrecursive_node(interface, material, nodeA)
            reflection = write_nonrecursive_node(interface, material, nodeB)
            matParams.inner.fresnel = FresnelParams(Vec2(ior, ior), POINTER(MaterialParams)(reflection),
                                                    POINTER(MaterialParams)(refraction))
            # TODO Check validity
            # TODO: extinction coefficient...
    elif node.inputs['Fac'].links[0].from_node.bl_idname.startswith('ShaderNodeTex'):
        # TODO: alpha from non-alpha channel texture!
        if hasAlphaAlready:
            raise Exception("material may not have more than one alpha blending (node '%s')"%(node.name))
        # We have a texture input at our hands: if it's alpha we can do smth with it!
        if node.inputs['Fac'].links[0].from_socket.name != 'Alpha':
            raise Exception("blend input for mix shader has to be 'Alpha' if it comes from a texture node (node '%s')"%(node.name))
        # Check if one of the layers is transparent and recursively call the material conversion
        if nodeA.bl_idname == 'ShaderNodeBsdfTransparent':
            if nodeB.bl_idname == 'ShaderNodeMixShader':
                matParams = write_mix_node(interface, material, nodeB, True)
            elif nodeB.bl_idname == 'ShaderNodeBsdfGlass':
                matParams = write_glass_node(interface, material, nodeB)
            else:
                matParams = write_nonrecursive_node(interface, material, nodeB)
        elif nodeB.bl_idname == 'ShaderNodeBsdfTransparent':
            if nodeA.bl_idname == 'ShaderNodeMixShader':
                matParams = write_mix_node(interface, material, nodeA, True)
            elif nodeA.bl_idname == 'ShaderNodeBsdfGlass':
                matParams = write_glass_node(interface, material, nodeA)
            else:
                matParams = write_nonrecursive_node(interface, material, nodeA)
        else:
            raise Exception("alpha blending requires one transparent node for the mix shader (node '%s')"%(node.name))
        # TODO: convert alpha channel to x channel!
        alpha = get_image_input(material, node, node.inputs['Fac'].links[0].from_node, 'Fac', True, False)
        matParams.alpha = add_texture_r32(interface, alpha)
    else:
        raise Exception("invalid mix-shader factor input (node '%s')"%(node.name))
        
    return matParams


def write_outer_medium(material, matParams):
    if hasattr(material, 'outer_medium') and material.outer_medium.enabled:
        matParams.outerMedium = Medium(Vec2(material.outer_medium.ior, material.outer_medium.ior),
                                       to_vec3(get_mapped_absorption(material.outer_medium.transmission)))

def add_materials(interface, materials):
    materialHdls = []
    for material in materials:
        # First get the node that actually determines the material properties
        outputNode = find_material_output_node(material)
        if outputNode is None:
            print("Skipping material '%s' (no output node)..."%(material.name))
            continue
        # Then handle surface properties: check the connections backwards
        if len(outputNode.inputs['Surface'].links) == 0:
            print("Skipping material '%s' (no connection to surface output)..."%(material.name))
            continue
        firstNode = outputNode.inputs['Surface'].links[0].from_node
        #try:
        if firstNode.bl_idname == 'ShaderNodeMixShader':
            matParams = write_mix_node(interface, material, firstNode, False)
        elif firstNode.bl_idname == 'ShaderNodeBsdfGlass':
            matParams = write_glass_node(interface, material, firstNode)
        else:
            matParams = write_nonrecursive_node(interface, material, firstNode)
        # TODO
        write_outer_medium(material, matParams)
        matHdl = interface.world_add_material(material.name, matParams)
        if matHdl == c_void_p(0):
            raise Exception("Failed to add material '%s'"%(material.name))
        materialHdls.append(matHdl)
        #except Exception as e:
        #    print("Exception: ",e)
        #    continue
    return materialHdls