# Copyright 2015 The TensorFlow Authors. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# ==============================================================================
"""Python utilities required by Keras."""
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import binascii
import codecs
import marshal
import os
import re
import types as python_types

import numpy as np
import six

from tensorflow.python.util import nest
from tensorflow.python.util import tf_contextlib
from tensorflow.python.util import tf_decorator
from tensorflow.python.util import tf_inspect

_GLOBAL_CUSTOM_OBJECTS = {}
_GLOBAL_CUSTOM_NAMES = {}

# Flag that determines whether to skip the NotImplementedError when calling
# get_config in custom models and layers. This is only enabled when saving to
# SavedModel, when the config isn't required.
_SKIP_FAILED_SERIALIZATION = False
# If a layer does not have a defined config, then the returned config will be a
# dictionary with the below key.
_LAYER_UNDEFINED_CONFIG_KEY = 'layer was saved without config'


class CustomObjectScope(object):
  """Provides a scope that changes to `_GLOBAL_CUSTOM_OBJECTS` cannot escape.

  Code within a `with` statement will be able to access custom objects
  by name. Changes to global custom objects persist
  within the enclosing `with` statement. At end of the `with` statement,
  global custom objects are reverted to state
  at beginning of the `with` statement.

  Example:

  Consider a custom object `MyObject` (e.g. a class):

  ```python
      with CustomObjectScope({'MyObject':MyObject}):
          layer = Dense(..., kernel_regularizer='MyObject')
          # save, load, etc. will recognize custom object by name
  ```
  """

  def __init__(self, *args):
    self.custom_objects = args
    self.backup = None

  def __enter__(self):
    self.backup = _GLOBAL_CUSTOM_OBJECTS.copy()
    for objects in self.custom_objects:
      _GLOBAL_CUSTOM_OBJECTS.update(objects)
    return self

  def __exit__(self, *args, **kwargs):
    _GLOBAL_CUSTOM_OBJECTS.clear()
    _GLOBAL_CUSTOM_OBJECTS.update(self.backup)


def custom_object_scope(*args):
  """Provides a scope that changes to `_GLOBAL_CUSTOM_OBJECTS` cannot escape.

  Convenience wrapper for `CustomObjectScope`.
  Code within a `with` statement will be able to access custom objects
  by name. Changes to global custom objects persist
  within the enclosing `with` statement. At end of the `with` statement,
  global custom objects are reverted to state
  at beginning of the `with` statement.

  Example:

  Consider a custom object `MyObject`

  ```python
      with custom_object_scope({'MyObject':MyObject}):
          layer = Dense(..., kernel_regularizer='MyObject')
          # save, load, etc. will recognize custom object by name
  ```

  Arguments:
      *args: Variable length list of dictionaries of name, class pairs to add to
        custom objects.

  Returns:
      Object of type `CustomObjectScope`.
  """
  return CustomObjectScope(*args)


def get_custom_objects():
  """Retrieves a live reference to the global dictionary of custom objects.

  Updating and clearing custom objects using `custom_object_scope`
  is preferred, but `get_custom_objects` can
  be used to directly access `_GLOBAL_CUSTOM_OBJECTS`.

  Example:

  ```python
      get_custom_objects().clear()
      get_custom_objects()['MyObject'] = MyObject
  ```

  Returns:
      Global dictionary of names to classes (`_GLOBAL_CUSTOM_OBJECTS`).
  """
  return _GLOBAL_CUSTOM_OBJECTS


def serialize_keras_class_and_config(cls_name, cls_config):
  """Returns the serialization of the class with the given config."""
  return {'class_name': cls_name, 'config': cls_config}


def register_keras_serializable(package='Custom', name=None):
  """Registers an object with the Keras serialization framework.

  This decorator injects the decorated class or function into the Keras custom
  object dictionary, so that it can be serialized and deserialized without
  needing an entry in the user-provided custom object dict. It also injects a
  function that Keras will call to get the object's serializable string key.

  Note that to be serialized and deserialized, classes must implement the
  `get_config()` method. Functions do not have this requirement.

  The object will be registered under the key 'package>name' where `name`,
  defaults to the object name if not passed.

  Arguments:
    package: The package that this class belongs to.
    name: The name to serialize this class under in this package. If None, the
      class's name will be used.

  Returns:
    A decorator that registers the decorated class with the passed names.
  """

  def decorator(arg):
    """Registers a class with the Keras serialization framework."""
    class_name = name if name is not None else arg.__name__
    registered_name = package + '>' + class_name

    if tf_inspect.isclass(arg) and not hasattr(arg, 'get_config'):
      raise ValueError(
          'Cannot register a class that does not have a get_config() method.')

    if registered_name in _GLOBAL_CUSTOM_OBJECTS:
      raise ValueError(
          '%s has already been registered to %s' %
          (registered_name, _GLOBAL_CUSTOM_OBJECTS[registered_name]))

    if arg in _GLOBAL_CUSTOM_NAMES:
      raise ValueError('%s has already been registered to %s' %
                       (arg, _GLOBAL_CUSTOM_NAMES[arg]))
    _GLOBAL_CUSTOM_OBJECTS[registered_name] = arg
    _GLOBAL_CUSTOM_NAMES[arg] = registered_name

    return arg

  return decorator


def get_registered_name(obj):
  """Returns the name registered to an object within the Keras framework.

  This function is part of the Keras serialization and deserialization
  framework. It maps objects to the string names associated with those objects
  for serialization/deserialization.

  Args:
    obj: The object to look up.

  Returns:
    The name associated with the object, or the default Python name if the
      object is not registered.
  """
  if obj in _GLOBAL_CUSTOM_NAMES:
    return _GLOBAL_CUSTOM_NAMES[obj]
  else:
    return obj.__name__


@tf_contextlib.contextmanager
def skip_failed_serialization():
  global _SKIP_FAILED_SERIALIZATION
  prev = _SKIP_FAILED_SERIALIZATION
  try:
    _SKIP_FAILED_SERIALIZATION = True
    yield
  finally:
    _SKIP_FAILED_SERIALIZATION = prev


def get_registered_object(name, custom_objects=None, module_objects=None):
  """Returns the class associated with `name` if it is registered with Keras.

  This function is part of the Keras serialization and deserialization
  framework. It maps strings to the objects associated with them for
  serialization/deserialization.

  Example:
  ```
  def from_config(cls, config, custom_objects=None):
    if 'my_custom_object_name' in config:
      config['hidden_cls'] = tf.keras.utils.get_registered_object(
          config['my_custom_object_name'], custom_objects=custom_objects)
  ```

  Args:
    name: The name to look up.
    custom_objects: A dictionary of custom objects to look the name up in.
      Generally, custom_objects is provided by the user.
    module_objects: A dictionary of custom objects to look the name up in.
      Generally, module_objects is provided by midlevel library implementers.

  Returns:
    An instantiable class associated with 'name', or None if no such class
      exists.
  """
  if name in _GLOBAL_CUSTOM_OBJECTS:
    return _GLOBAL_CUSTOM_OBJECTS[name]
  elif custom_objects and name in custom_objects:
    return custom_objects[name]
  elif module_objects and name in module_objects:
    return module_objects[name]
  return None


def serialize_keras_object(instance):
  """Serialize Keras object into JSON."""
  _, instance = tf_decorator.unwrap(instance)
  if instance is None:
    return None

  if hasattr(instance, 'get_config'):
    name = get_registered_name(instance.__class__)
    try:
      config = instance.get_config()
    except NotImplementedError as e:
      if _SKIP_FAILED_SERIALIZATION:
        return serialize_keras_class_and_config(
            name, {_LAYER_UNDEFINED_CONFIG_KEY: True})
      raise e
    serialization_config = {}
    for key, item in config.items():
      if isinstance(item, six.string_types):
        serialization_config[key] = item
        continue

      # Any object of a different type needs to be converted to string or dict
      # for serialization (e.g. custom functions, custom classes)
      try:
        serialized_item = serialize_keras_object(item)
        if isinstance(serialized_item, dict) and not isinstance(item, dict):
          serialized_item['__passive_serialization__'] = True
        serialization_config[key] = serialized_item
      except ValueError:
        serialization_config[key] = item

    name = get_registered_name(instance.__class__)
    return serialize_keras_class_and_config(name, serialization_config)
  if hasattr(instance, '__name__'):
    return get_registered_name(instance)
  raise ValueError('Cannot serialize', instance)


def get_custom_objects_by_name(item, custom_objects=None):
  """Returns the item if it is in either local or global custom objects."""
  if item in _GLOBAL_CUSTOM_OBJECTS:
    return _GLOBAL_CUSTOM_OBJECTS[item]
  elif custom_objects and item in custom_objects:
    return custom_objects[item]
  return None


def class_and_config_for_serialized_keras_object(
    config,
    module_objects=None,
    custom_objects=None,
    printable_module_name='object'):
  """Returns the class name and config for a serialized keras object."""
  if (not isinstance(config, dict) or 'class_name' not in config or
      'config' not in config):
    raise ValueError('Improper config format: ' + str(config))

  class_name = config['class_name']
  cls = get_registered_object(class_name, custom_objects, module_objects)
  if cls is None:
    raise ValueError('Unknown ' + printable_module_name + ': ' + class_name)

  cls_config = config['config']
  deserialized_objects = {}
  for key, item in cls_config.items():
    if isinstance(item, dict) and '__passive_serialization__' in item:
      deserialized_objects[key] = deserialize_keras_object(
          item,
          module_objects=module_objects,
          custom_objects=custom_objects,
          printable_module_name='config_item')
    # TODO(momernick): Should this also have 'module_objects'?
    elif (isinstance(item, six.string_types) and
          tf_inspect.isfunction(get_registered_object(item, custom_objects))):
      # Handle custom functions here. When saving functions, we only save the
      # function's name as a string. If we find a matching string in the custom
      # objects during deserialization, we convert the string back to the
      # original function.
      # Note that a potential issue is that a string field could have a naming
      # conflict with a custom function name, but this should be a rare case.
      # This issue does not occur if a string field has a naming conflict with
      # a custom object, since the config of an object will always be a dict.
      deserialized_objects[key] = get_registered_object(item, custom_objects)
  for key, item in deserialized_objects.items():
    cls_config[key] = deserialized_objects[key]

  return (cls, cls_config)


def deserialize_keras_object(identifier,
                             module_objects=None,
                             custom_objects=None,
                             printable_module_name='object'):
  if identifier is None:
    return None

  if isinstance(identifier, dict):
    # In this case we are dealing with a Keras config dictionary.
    config = identifier
    (cls, cls_config) = class_and_config_for_serialized_keras_object(
        config, module_objects, custom_objects, printable_module_name)

    if hasattr(cls, 'from_config'):
      arg_spec = tf_inspect.getfullargspec(cls.from_config)
      custom_objects = custom_objects or {}

      if 'custom_objects' in arg_spec.args:
        return cls.from_config(
            cls_config,
            custom_objects=dict(
                list(_GLOBAL_CUSTOM_OBJECTS.items()) +
                list(custom_objects.items())))
      with CustomObjectScope(custom_objects):
        return cls.from_config(cls_config)
    else:
      # Then `cls` may be a function returning a class.
      # in this case by convention `config` holds
      # the kwargs of the function.
      custom_objects = custom_objects or {}
      with CustomObjectScope(custom_objects):
        return cls(**cls_config)
  elif isinstance(identifier, six.string_types):
    object_name = identifier
    if custom_objects and object_name in custom_objects:
      obj = custom_objects.get(object_name)
    elif object_name in _GLOBAL_CUSTOM_OBJECTS:
      obj = _GLOBAL_CUSTOM_OBJECTS[object_name]
    else:
      obj = module_objects.get(object_name)
      if obj is None:
        raise ValueError('Unknown ' + printable_module_name + ':' + object_name)
    # Classes passed by name are instantiated with no args, functions are
    # returned as-is.
    if tf_inspect.isclass(obj):
      return obj()
    return obj
  elif tf_inspect.isfunction(identifier):
    # If a function has already been deserialized, return as is.
    return identifier
  else:
    raise ValueError('Could not interpret serialized %s: %s' %
                     (printable_module_name, identifier))


def func_dump(func):
  """Serializes a user defined function.

  Arguments:
      func: the function to serialize.

  Returns:
      A tuple `(code, defaults, closure)`.
  """
  if os.name == 'nt':
    raw_code = marshal.dumps(func.__code__).replace(b'\\', b'/')
    code = codecs.encode(raw_code, 'base64').decode('ascii')
  else:
    raw_code = marshal.dumps(func.__code__)
    code = codecs.encode(raw_code, 'base64').decode('ascii')
  defaults = func.__defaults__
  if func.__closure__:
    closure = tuple(c.cell_contents for c in func.__closure__)
  else:
    closure = None
  return code, defaults, closure


def func_load(code, defaults=None, closure=None, globs=None):
  """Deserializes a user defined function.

  Arguments:
      code: bytecode of the function.
      defaults: defaults of the function.
      closure: closure of the function.
      globs: dictionary of global objects.

  Returns:
      A function object.
  """
  if isinstance(code, (tuple, list)):  # unpack previous dump
    code, defaults, closure = code
    if isinstance(defaults, list):
      defaults = tuple(defaults)

  def ensure_value_to_cell(value):
    """Ensures that a value is converted to a python cell object.

    Arguments:
        value: Any value that needs to be casted to the cell type

    Returns:
        A value wrapped as a cell object (see function "func_load")
    """

    def dummy_fn():
      # pylint: disable=pointless-statement
      value  # just access it so it gets captured in .__closure__

    cell_value = dummy_fn.__closure__[0]
    if not isinstance(value, type(cell_value)):
      return cell_value
    return value

  if closure is not None:
    closure = tuple(ensure_value_to_cell(_) for _ in closure)
  try:
    raw_code = codecs.decode(code.encode('ascii'), 'base64')
  except (UnicodeEncodeError, binascii.Error):
    raw_code = code.encode('raw_unicode_escape')
  code = marshal.loads(raw_code)
  if globs is None:
    globs = globals()
  return python_types.FunctionType(
      code, globs, name=code.co_name, argdefs=defaults, closure=closure)


def has_arg(fn, name, accept_all=False):
  """Checks if a callable accepts a given keyword argument.

  Arguments:
      fn: Callable to inspect.
      name: Check if `fn` can be called with `name` as a keyword argument.
      accept_all: What to return if there is no parameter called `name` but the
        function accepts a `**kwargs` argument.

  Returns:
      bool, whether `fn` accepts a `name` keyword argument.
  """
  arg_spec = tf_inspect.getfullargspec(fn)
  if accept_all and arg_spec.varkw is not None:
    return True
  return name in arg_spec.args


def make_batches(size, batch_size):
  """Returns a list of batch indices (tuples of indices).

  Arguments:
      size: Integer, total size of the data to slice into batches.
      batch_size: Integer, batch size.

  Returns:
      A list of tuples of array indices.
  """
  num_batches = int(np.ceil(size / float(batch_size)))
  return [(i * batch_size, min(size, (i + 1) * batch_size))
          for i in range(0, num_batches)]


def slice_arrays(arrays, start=None, stop=None):
  """Slice an array or list of arrays.

  This takes an array-like, or a list of
  array-likes, and outputs:
      - arrays[start:stop] if `arrays` is an array-like
      - [x[start:stop] for x in arrays] if `arrays` is a list

  Can also work on list/array of indices: `slice_arrays(x, indices)`

  Arguments:
      arrays: Single array or list of arrays.
      start: can be an integer index (start index) or a list/array of indices
      stop: integer (stop index); should be None if `start` was a list.

  Returns:
      A slice of the array(s).

  Raises:
      ValueError: If the value of start is a list and stop is not None.
  """
  if arrays is None:
    return [None]
  if isinstance(start, list) and stop is not None:
    raise ValueError('The stop argument has to be None if the value of start '
                     'is a list.')
  elif isinstance(arrays, list):
    if hasattr(start, '__len__'):
      # hdf5 datasets only support list objects as indices
      if hasattr(start, 'shape'):
        start = start.tolist()
      return [None if x is None else x[start] for x in arrays]
    return [
        None if x is None else
        None if not hasattr(x, '__getitem__') else x[start:stop] for x in arrays
    ]
  else:
    if hasattr(start, '__len__'):
      if hasattr(start, 'shape'):
        start = start.tolist()
      return arrays[start]
    if hasattr(start, '__getitem__'):
      return arrays[start:stop]
    return [None]


def to_list(x):
  """Normalizes a list/tensor into a list.

  If a tensor is passed, we return
  a list of size 1 containing the tensor.

  Arguments:
      x: target object to be normalized.

  Returns:
      A list.
  """
  if isinstance(x, list):
    return x
  return [x]


def to_snake_case(name):
  intermediate = re.sub('(.)([A-Z][a-z0-9]+)', r'\1_\2', name)
  insecure = re.sub('([a-z])([A-Z])', r'\1_\2', intermediate).lower()
  # If the class is private the name starts with "_" which is not secure
  # for creating scopes. We prefix the name with "private" in this case.
  if insecure[0] != '_':
    return insecure
  return 'private' + insecure


def is_all_none(structure):
  iterable = nest.flatten(structure)
  # We cannot use Python's `any` because the iterable may return Tensors.
  for element in iterable:
    if element is not None:
      return False
  return True


def check_for_unexpected_keys(name, input_dict, expected_values):
  unknown = set(input_dict.keys()).difference(expected_values)
  if unknown:
    raise ValueError('Unknown entries in {} dictionary: {}. Only expected '
                     'following keys: {}'.format(name, list(unknown),
                                                 expected_values))


def validate_kwargs(kwargs,
                    allowed_kwargs,
                    error_message='Keyword argument not understood:'):
  """Checks that all keyword arguments are in the set of allowed keys."""
  for kwarg in kwargs:
    if kwarg not in allowed_kwargs:
      raise TypeError(error_message, kwarg)


def validate_config(config):
  """Determines whether config appears to be a valid layer config."""
  return isinstance(config, dict) and _LAYER_UNDEFINED_CONFIG_KEY not in config


def default(method):
  """Decorates a method to detect overrides in subclasses."""
  method._is_default = True  # pylint: disable=protected-access
  return method


def is_default(method):
  """Check if a method is decorated with the `default` wrapper."""
  return getattr(method, '_is_default', False)
