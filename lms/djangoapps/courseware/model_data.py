"""
Classes to provide the LMS runtime data storage to XBlocks
"""

import json
from abc import abstractmethod, ABCMeta
from collections import defaultdict
from itertools import chain
from .models import (
    StudentModule,
    XModuleUserStateSummaryField,
    XModuleStudentPrefsField,
    XModuleStudentInfoField
)
import logging
from opaque_keys.edx.keys import CourseKey
from opaque_keys.edx.block_types import BlockTypeKeyV1
from opaque_keys.edx.asides import AsideUsageKeyV1
from contracts import contract, new_contract

from django.db import DatabaseError

from xblock.runtime import KeyValueStore
from xblock.exceptions import KeyValueMultiSaveError, InvalidScopeError
from xblock.fields import Scope, UserScope
from xmodule.modulestore.django import modulestore
from xblock.core import XBlockAside

log = logging.getLogger(__name__)


class InvalidWriteError(Exception):
    """
    Raised to indicate that writing to a particular key
    in the KeyValueStore is disabled
    """


def chunks(items, chunk_size):
    """
    Yields the values from items in chunks of size chunk_size
    """
    items = list(items)
    return (items[i:i + chunk_size] for i in xrange(0, len(items), chunk_size))


def _query(model_class, select_for_update, **kwargs):
    """
    Queries model_class with **kwargs, optionally adding select_for_update if
    `select_for_update` is True.
    """
    query = model_class.objects
    if select_for_update:
        query = query.select_for_update()
    query = query.filter(**kwargs)
    return query

def _chunked_query(model_class, select_for_update, chunk_field, items, chunk_size=500, **kwargs):
    """
    Queries model_class with `chunk_field` set to chunks of size `chunk_size`,
    and all other parameters from `**kwargs`.

    This works around a limitation in sqlite3 on the number of parameters
    that can be put into a single query.
    """
    res = chain.from_iterable(
        _query(model_class, select_for_update, **dict([(chunk_field, chunk)] + kwargs.items()))
        for chunk in chunks(items, chunk_size)
    )
    return res


def _all_usage_keys(descriptors, aside_types):
    """
    Return a set of all usage_ids for the `descriptors` and for
    as all asides in `aside_types` for those descriptors.
    """
    usage_ids = set()
    for descriptor in descriptors:
        usage_ids.add(descriptor.scope_ids.usage_id)

        for aside_type in aside_types:
            usage_ids.add(AsideUsageKeyV1(descriptor.scope_ids.usage_id, aside_type))

    return usage_ids


def _all_block_types(descriptors, aside_types):
    """
    Return a set of all block_types for the supplied `descriptors` and for
    the asides types in `aside_types` associated with those descriptors.
    """
    block_types = set()
    for descriptor in descriptors:
        block_types.add(BlockTypeKeyV1(descriptor.entry_point, descriptor.scope_ids.block_type))

    for aside_type in aside_types:
        block_types.add(BlockTypeKeyV1(XBlockAside.entry_point, aside_type))

    return block_types


class DjangoKeyValueStore(KeyValueStore):
    """
    This KeyValueStore will read and write data in the following scopes to django models
        Scope.user_state_summary
        Scope.user_state
        Scope.preferences
        Scope.user_info

    Access to any other scopes will raise an InvalidScopeError

    Data for Scope.user_state is stored as StudentModule objects via the django orm.

    Data for the other scopes is stored in individual objects that are named for the
    scope involved and have the field name as a key

    If the key isn't found in the expected table during a read or a delete, then a KeyError will be raised
    """

    _allowed_scopes = (
        Scope.user_state_summary,
        Scope.user_state,
        Scope.preferences,
        Scope.user_info,
    )

    def __init__(self, field_data_cache):
        self._field_data_cache = field_data_cache

    def get(self, key):
        if key.scope not in self._allowed_scopes:
            raise InvalidScopeError(key)

        return self._field_data_cache.get(key)

    def set(self, key, value):
        """
        Set a single value in the KeyValueStore
        """
        self.set_many({key: value})

    def set_many(self, kv_dict):
        """
        Provide a bulk save mechanism.

        `kv_dict`: A dictionary of dirty fields that maps
          xblock.KvsFieldData._key : value

        """
        for key in kv_dict:
            # Check key for validity
            if key.scope not in self._allowed_scopes:
                raise InvalidScopeError(key)

        self._field_data_cache.set_many(kv_dict)

    def delete(self, key):
        if key.scope not in self._allowed_scopes:
            raise InvalidScopeError(key)

        self._field_data_cache.delete(key)

    def has(self, key):
        if key.scope not in self._allowed_scopes:
            raise InvalidScopeError(key)

        return self._field_data_cache.has(key)


new_contract("DjangoKeyValueStore", DjangoKeyValueStore)
new_contract("DjangoKeyValueStore_Key", DjangoKeyValueStore.Key)


class DjangoOrmFieldCache(object):
    """
    Baseclass for Scope-specific field cache objects that are based on
    single-row-per-field Django ORM objects.
    """
    __metaclass__ = ABCMeta

    def __init__(self):
        self._cache = {}

    def cache_fields(self, fields, xblocks, aside_types):
        """
        Load all fields specified by ``fields`` for the supplied ``xblocks``
        and ``aside_types`` into this cache.

        Arguments:
            fields (list of str): Field names to cache.
            xblocks (list of :class:`XBlock`): XBlocks to cache fields for.
            aside_types (list of str): Aside types to cache fields for.
        """
        for field_object in self._read_objects(fields, xblocks, aside_types):
            self._cache[self._cache_key_for_field_object(field_object)] = field_object

    @contract(kvs_key=DjangoKeyValueStore.Key)
    def get(self, kvs_key):
        return self._cache.get(self._cache_key_for_kvs_key(kvs_key))

    @contract(kvs_key=DjangoKeyValueStore.Key)
    def set(self, kvs_key, value):
        self._cache[self._cache_key_for_kvs_key(kvs_key)] = value

    @contract(kvs_key=DjangoKeyValueStore.Key)
    def delete(self, kvs_key):
        """
        Delete the value specified by `kvs_key`.

        Arguments:
            kvs_key (`DjangoKeyValueStore.Key`): The field value to delete

        Raises: KeyError if key isn't found in the cache
        """

        cache_key = self._cache_key_for_kvs_key(kvs_key)
        field_object = self._cache.get(cache_key)
        if field_object is None:
            raise KeyError(kvs_key.field_name)

        field_object.delete()
        del self._cache[cache_key]

    @contract(kvs_key=DjangoKeyValueStore.Key, returns=bool)
    def has(self, kvs_key):
        """
        Return whether the specified `kvs_key` is set.

        Arguments:
            kvs_key (`DjangoKeyValueStore.Key`): The field value to delete

        Returns: bool
        """
        return self._cache_key_for_kvs_key(kvs_key) in self._cache

    def __len__(self):
        return len(self._cache)

    @abstractmethod
    def _create_object(self, kvs_key, value):
        """
        Create a new object to add to the cache (which should record
        the specified field ``value`` for the field identified by
        ``kvs_key``).

        Arguments:
            kvs_key (:class:`DjangoKeyValueStore.Key`): Which field to create an entry for
            value: What value to record in the field
        """
        raise NotImplementedError()

    @abstractmethod
    def _read_objects(self, fields, xblocks, aside_types):
        """
        Return an iterator for all objects stored in the underlying datastore
        for the ``fields`` on the ``xblocks`` and the ``aside_types`` associated
        with them.

        Arguments:
            fields (list of str): Field names to return values for
            xblocks (list of :class:`~XBlock`): XBlocks to load fields for
            aside_types (list of str): Asides to load field for (which annotate the supplied
                xblocks).
        """
        raise NotImplementedError()

    @abstractmethod
    def _cache_key_for_field_object(self, field_object):
        """
        Return the key used in this DjangoOrmFieldCache to store the specified field_object.

        Arguments:
            field_object: A Django model instance that stores the data for fields in this cache
        """
        raise NotImplementedError()

    @abstractmethod
    def _cache_key_for_kvs_key(self, key):
        """
        Return the key used in this DjangoOrmFieldCache for the specified KeyValueStore key.

        Arguments:
            key (:class:`~DjangoKeyValueStore.Key`): The key representing the cached field
        """
        raise NotImplementedError()


class UserStateCache(DjangoOrmFieldCache):
    """
    Cache for Scope.user_state xblock field data.
    """
    def __init__(self, user, course_id, select_for_update=False):
        super(UserStateCache, self).__init__()
        self.course_id = course_id
        self.user = user
        self.select_for_update = select_for_update

    def _read_objects(self, fields, xblocks, aside_types):
        """
        Return an iterator for all objects stored in the underlying datastore
        for the ``fields`` on the ``xblocks`` and the ``aside_types`` associated
        with them.

        Arguments:
            fields (list of str): Field names to return values for
            xblocks (list of :class:`~XBlock`): XBlocks to load fields for
            aside_types (list of str): Asides to load field for (which annotate the supplied
                xblocks).
        """
        return _chunked_query(
            StudentModule,
            self.select_for_update,
            'module_state_key__in',
            _all_usage_keys(xblocks, aside_types),
            course_id=self.course_id,
            student=self.user.pk,
        )

    def _cache_key_for_field_object(self, field_object):
        return field_object.module_state_key.map_into_course(self.course_id)

    def _cache_key_for_kvs_key(self, key):
        """
        Return the key used in this DjangoOrmFieldCache for the specified KeyValueStore key.

        Arguments:
            key (:class:`~DjangoKeyValueStore.Key`): The key representing the cached field
        """
        return key.block_scope_id

    @contract(kvs_key=DjangoKeyValueStore.Key)
    def delete(self, kvs_key):
        """
        Delete the value specified by `kvs_key`.

        Arguments:
            kvs_key (`DjangoKeyValueStore.Key`): The field value to delete

        Raises: KeyError if key isn't found in the cache
        """

        field_object = self._cache.get(self._cache_key_for_kvs_key(kvs_key))
        if field_object is None:
            raise KeyError(kvs_key.field_name)

        state = json.loads(field_object.state)
        del state[kvs_key.field_name]
        field_object.state = json.dumps(state)
        field_object.save()

    @contract(kvs_key=DjangoKeyValueStore.Key, returns=bool)
    def has(self, kvs_key):
        """
        Return whether the specified `kvs_key` is set.

        Arguments:
            kvs_key (`DjangoKeyValueStore.Key`): The field value to delete

        Returns: bool
        """
        field_object = self._cache.get(self._cache_key_for_kvs_key(kvs_key))
        if field_object is None:
            return False

        return kvs_key.field_name in json.loads(field_object.state)

class UserStateSummaryCache(DjangoOrmFieldCache):
    """
    Cache for Scope.user_state_summary xblock field data.
    """
    def __init__(self, course_id, select_for_update=False):
        super(UserStateSummaryCache, self).__init__()
        self.course_id = course_id
        self.select_for_update = select_for_update

    def _read_objects(self, fields, xblocks, aside_types):
        """
        Return an iterator for all objects stored in the underlying datastore
        for the ``fields`` on the ``xblocks`` and the ``aside_types`` associated
        with them.

        Arguments:
            fields (list of str): Field names to return values for
            xblocks (list of :class:`~XBlock`): XBlocks to load fields for
            aside_types (list of str): Asides to load field for (which annotate the supplied
                xblocks).
        """
        return _chunked_query(
            XModuleUserStateSummaryField,
            self.select_for_update,
            'usage_id__in',
            _all_usage_keys(xblocks, aside_types),
            field_name__in=set(field.name for field in fields),
        )

    def _cache_key_for_field_object(self, field_object):
        """
        Return the key used in this DjangoOrmFieldCache to store the specified field_object.

        Arguments:
            field_object: A Django model instance that stores the data for fields in this cache
        """
        return (field_object.usage_id.map_into_course(self.course_id), field_object.field_name)

    def _cache_key_for_kvs_key(self, key):
        """
        Return the key used in this DjangoOrmFieldCache for the specified KeyValueStore key.

        Arguments:
            key (:class:`~DjangoKeyValueStore.Key`): The key representing the cached field
        """
        return (key.block_scope_id, key.field_name)


class PreferencesCache(DjangoOrmFieldCache):
    """
    Cache for Scope.preferences xblock field data.
    """
    def __init__(self, user, select_for_update=False):
        super(PreferencesCache, self).__init__()
        self.user = user
        self.select_for_update = select_for_update

    def _read_objects(self, fields, xblocks, aside_types):
        """
        Return an iterator for all objects stored in the underlying datastore
        for the ``fields`` on the ``xblocks`` and the ``aside_types`` associated
        with them.

        Arguments:
            fields (list of str): Field names to return values for
            xblocks (list of :class:`~XBlock`): XBlocks to load fields for
            aside_types (list of str): Asides to load field for (which annotate the supplied
                xblocks).
        """
        return _chunked_query(
            XModuleStudentPrefsField,
            self.select_for_update,
            'module_type__in',
            _all_block_types(xblocks, aside_types),
            student=self.user.pk,
            field_name__in=set(field.name for field in fields),
        )

    def _cache_key_for_field_object(self, field_object):
        """
        Return the key used in this DjangoOrmFieldCache to store the specified field_object.

        Arguments:
            field_object: A Django model instance that stores the data for fields in this cache
        """
        return (field_object.module_type, field_object.field_name)

    def _cache_key_for_kvs_key(self, key):
        """
        Return the key used in this DjangoOrmFieldCache for the specified KeyValueStore key.

        Arguments:
            key (:class:`~DjangoKeyValueStore.Key`): The key representing the cached field
        """
        return (BlockTypeKeyV1(key.block_family, key.block_scope_id), key.field_name)


class UserInfoCache(DjangoOrmFieldCache):
    """
    Cache for Scope.user_info xblock field data
    """
    def __init__(self, user, select_for_update=False):
        super(UserInfoCache, self).__init__()
        self.user = user
        self.select_for_update = select_for_update

    def _read_objects(self, fields, xblocks, aside_types):
        """
        Return an iterator for all objects stored in the underlying datastore
        for the ``fields`` on the ``xblocks`` and the ``aside_types`` associated
        with them.

        Arguments:
            fields (list of str): Field names to return values for
            xblocks (list of :class:`~XBlock`): XBlocks to load fields for
            aside_types (list of str): Asides to load field for (which annotate the supplied
                xblocks).
        """
        return _query(
            XModuleStudentInfoField,
            self.select_for_update,
            student=self.user.pk,
            field_name__in=set(field.name for field in fields),
        )

    def _cache_key_for_field_object(self, field_object):
        """
        Return the key used in this DjangoOrmFieldCache to store the specified field_object.

        Arguments:
            field_object: A Django model instance that stores the data for fields in this cache
        """
        return field_object.field_name

    def _cache_key_for_kvs_key(self, key):
        """
        Return the key used in this DjangoOrmFieldCache for the specified KeyValueStore key.

        Arguments:
            key (:class:`~DjangoKeyValueStore.Key`): The key representing the cached field
        """
        return key.field_name


class FieldDataCache(object):
    """
    A cache of django model objects needed to supply the data
    for a module and its decendants
    """
    def __init__(self, descriptors, course_id, user, select_for_update=False, asides=None):
        '''
        Find any courseware.models objects that are needed by any descriptor
        in descriptors. Attempts to minimize the number of queries to the database.
        Note: Only modules that have store_state = True or have shared
        state will have a StudentModule.

        Arguments
        descriptors: A list of XModuleDescriptors.
        course_id: The id of the current course
        user: The user for which to cache data
        select_for_update: True if rows should be locked until end of transaction
        asides: The list of aside types to load, or None to prefetch no asides.
        '''
        self.select_for_update = select_for_update

        if asides is None:
            self.asides = []
        else:
            self.asides = asides

        assert isinstance(course_id, CourseKey)
        self.course_id = course_id
        self.user = user

        self.cache = {
            Scope.user_state: UserStateCache(
                self.user,
                self.course_id,
                self.select_for_update,
            ),
            Scope.user_info: UserInfoCache(
                self.user,
                self.select_for_update,
            ),
            Scope.preferences: PreferencesCache(
                self.user,
                self.select_for_update,
            ),
            Scope.user_state_summary: UserStateSummaryCache(
                self.course_id,
                self.select_for_update,
            ),
        }
        self.add_descriptors_to_cache(descriptors)

    def add_descriptors_to_cache(self, descriptors):
        """
        Add all `descriptors` to this FieldDataCache.
        """
        if self.user.is_authenticated():
            for scope, fields in self._fields_to_cache(descriptors).items():
                if scope not in self.cache:
                    continue

                self.cache[scope].cache_fields(fields, descriptors, self.asides)

    def add_descriptor_descendents(self, descriptor, depth=None, descriptor_filter=lambda descriptor: True):
        """
        Add all descendents of `descriptor` to this FieldDataCache.

        Arguments:
            descriptor: An XModuleDescriptor
            depth is the number of levels of descendent modules to load StudentModules for, in addition to
                the supplied descriptor. If depth is None, load all descendent StudentModules
            descriptor_filter is a function that accepts a descriptor and return wether the StudentModule
                should be cached
        """

        def get_child_descriptors(descriptor, depth, descriptor_filter):
            """
            Return a list of all child descriptors down to the specified depth
            that match the descriptor filter. Includes `descriptor`

            descriptor: The parent to search inside
            depth: The number of levels to descend, or None for infinite depth
            descriptor_filter(descriptor): A function that returns True
                if descriptor should be included in the results
            """
            if descriptor_filter(descriptor):
                descriptors = [descriptor]
            else:
                descriptors = []

            if depth is None or depth > 0:
                new_depth = depth - 1 if depth is not None else depth

                for child in descriptor.get_children() + descriptor.get_required_module_descriptors():
                    descriptors.extend(get_child_descriptors(child, new_depth, descriptor_filter))

            return descriptors

        with modulestore().bulk_operations(descriptor.location.course_key):
            descriptors = get_child_descriptors(descriptor, depth, descriptor_filter)

        self.add_descriptors_to_cache(descriptors)

    @classmethod
    def cache_for_descriptor_descendents(cls, course_id, user, descriptor, depth=None,
                                         descriptor_filter=lambda descriptor: True,
                                         select_for_update=False, asides=None):
        """
        course_id: the course in the context of which we want StudentModules.
        user: the django user for whom to load modules.
        descriptor: An XModuleDescriptor
        depth is the number of levels of descendent modules to load StudentModules for, in addition to
            the supplied descriptor. If depth is None, load all descendent StudentModules
        descriptor_filter is a function that accepts a descriptor and return wether the StudentModule
            should be cached
        select_for_update: Flag indicating whether the rows should be locked until end of transaction
        """
        cache = FieldDataCache([], course_id, user, select_for_update, asides=asides)
        cache.add_descriptor_descendents(descriptor, depth, descriptor_filter)
        return cache

    def _fields_to_cache(self, descriptors):
        """
        Returns a map of scopes to fields in that scope that should be cached
        """
        scope_map = defaultdict(set)
        for descriptor in descriptors:
            for field in descriptor.fields.values():
                scope_map[field.scope].add(field)
        return scope_map

    @contract(key=DjangoKeyValueStore.Key)
    def get(self, key):
        '''
        Load the field value specified by `key`.

        Arguments:
            key (`DjangoKeyValueStore.Key`): The field value to load

        Returns: The found value
        Raises: KeyError if key isn't found in the cache
        '''

        if key.scope.user == UserScope.ONE and not self.user.is_anonymous():
            # If we're getting user data, we expect that the key matches the
            # user we were constructed for.
            assert key.user_id == self.user.id

        if key.scope not in self.cache:
            raise KeyError(key.field_name)

        field_object = self.cache[key.scope].get(key)
        if field_object is None:
            raise KeyError(key.field_name)

        if key.scope == Scope.user_state:
            return json.loads(field_object.state)[key.field_name]
        else:
            return json.loads(field_object.value)

    @contract(kv_dict="dict(DjangoKeyValueStore_Key: *)")
    def set_many(self, kv_dict):
        """
        Set all of the fields specified by the keys of `kv_dict` to the values
        in that dict.

        Arguments:
            kv_dict (dict): dict mapping from `DjangoKeyValueStore.Key`s to field values
        Raises: DatabaseError if any fields fail to save
        """

        saved_fields = []
        # field_objects maps a field_object to a list of associated fields
        field_objects = dict()
        for key in kv_dict:
            # If the field is valid and isn't already in the dictionary, add it.

            if key.scope.user == UserScope.ONE and not self.user.is_anonymous():
                # If we're getting user data, we expect that the key matches the
                # user we were constructed for.
                assert key.user_id == self.user.id

            if key.scope not in self.cache:
                continue

            field_object = self.cache[key.scope].get(key)

            if field_object is None:
                if key.scope == Scope.user_state:
                    field_object, __ = StudentModule.objects.get_or_create(
                        course_id=self.course_id,
                        student_id=key.user_id,
                        module_state_key=key.block_scope_id,
                        defaults={
                            'state': json.dumps({}),
                            'module_type': key.block_scope_id.block_type,
                        },
                    )
                elif key.scope == Scope.user_state_summary:
                    field_object, __ = XModuleUserStateSummaryField.objects.get_or_create(
                        field_name=key.field_name,
                        usage_id=key.block_scope_id
                    )
                elif key.scope == Scope.preferences:
                    field_object, __ = XModuleStudentPrefsField.objects.get_or_create(
                        field_name=key.field_name,
                        module_type=BlockTypeKeyV1(key.block_family, key.block_scope_id),
                        student_id=key.user_id,
                    )
                elif key.scope == Scope.user_info:
                    field_object, __ = XModuleStudentInfoField.objects.get_or_create(
                        field_name=key.field_name,
                        student_id=key.user_id,
                    )

                self.cache[key.scope].set(key, field_object)

            if field_object not in field_objects.keys():
                field_objects[field_object] = []

            # Update the list of associated fields
            field_objects[field_object].append(key)

            # Special case when scope is for the user state, because this scope saves fields in a single row
            if key.scope == Scope.user_state:
                state = json.loads(field_object.state)
                state[key.field_name] = kv_dict[key]
                field_object.state = json.dumps(state)
            else:
                # The remaining scopes save fields on different rows, so
                # we don't have to worry about conflicts
                field_object.value = json.dumps(kv_dict[key])

        for field_object in field_objects:
            try:
                # Save the field object that we made above
                field_object.save()
                # If save is successful on this scope, add the saved fields to
                # the list of successful saves
                saved_fields.extend([key.field_name for key in field_objects[field_object]])
            except DatabaseError:
                log.exception('Error saving fields %r', field_objects[field_object])
                raise KeyValueMultiSaveError(saved_fields)

    @contract(key=DjangoKeyValueStore.Key)
    def delete(self, key):
        """
        Delete the value specified by `key`.

        Arguments:
            key (`DjangoKeyValueStore.Key`): The field value to delete

        Raises: KeyError if key isn't found in the cache
        """

        if key.scope.user == UserScope.ONE and not self.user.is_anonymous():
            # If we're getting user data, we expect that the key matches the
            # user we were constructed for.
            assert key.user_id == self.user.id

        if key.scope not in self.cache:
            raise KeyError(key.field_name)

        self.cache[key.scope].delete(key)

    @contract(key=DjangoKeyValueStore.Key, returns=bool)
    def has(self, key):
        """
        Return whether the specified `key` is set.

        Arguments:
            key (`DjangoKeyValueStore.Key`): The field value to delete

        Returns: bool
        """

        if key.scope.user == UserScope.ONE and not self.user.is_anonymous():
            # If we're getting user data, we expect that the key matches the
            # user we were constructed for.
            assert key.user_id == self.user.id

        if key.scope not in self.cache:
            return False

        return self.cache[key.scope].has(key)
