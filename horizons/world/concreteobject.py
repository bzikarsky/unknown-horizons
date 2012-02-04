# ###################################################
# Copyright (C) 2012 The Unknown Horizons Team
# team@unknown-horizons.org
# This file is part of Unknown Horizons.
#
# Unknown Horizons is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the
# Free Software Foundation, Inc.,
# 51 Franklin St, Fifth Floor, Boston, MA  02110-1301  USA
# ###################################################

from fife import fife

from horizons.scheduler import Scheduler
from horizons.util import WorldObject, Callback, ActionSetLoader
from horizons.gui.tabs import BuildRelatedTab
from horizons.world.status import StatusIcon
from horizons.world.units import UnitClass
from random import randint

class ConcreteObject(WorldObject):
	"""Class for concrete objects like Units or Buildings.
	"Concrete" here means "you can touch it", e.g. a Warehouse is a ConcreteObject,
	a Settlement isn't.
	All such objects have positions, so Islands are no ConcreteObjects for technical reasons.

	Assumes that object has a member _instance.
	"""
	movable = False # whether instance can move
	tabs = tuple() # iterable collection of classes of tabs to show when selected
	enemy_tabs = tuple() # same as tabs, but used when clicking on enemy's instances
	is_unit = False
	is_building = False
	is_selectable = False
	has_status_icon = False

	def __init__(self, session, **kwargs):
		"""
		@param session: Session instance this obj belongs to
		"""
		super(ConcreteObject, self).__init__(**kwargs)
		from horizons.session import Session
		assert isinstance(session, Session)
		self.session = session
		self.__init()

	def __init(self):
		self._instance = None # overwrite in subclass __init[__]
		self._action = 'idle' # Default action is idle
		self._action_set_id = self.get_random_action_set()[0]

		related_building = self.session.db.cached_query("SELECT building FROM related_buildings where building = ?", self.id)

		if len(related_building) > 0 and BuildRelatedTab not in self.__class__.tabs:
			self.__class__.tabs += (BuildRelatedTab,)

		self._status_icon_key = "status_"+str(self.worldid)
		self._status_icon_renderer = self.session.view.renderer['GenericRenderer']

		# only buildings for now
		if self.is_building and not self.id in self.session.db.get_status_icon_exclusions():
			self.has_status_icon = True
			# update now
			Scheduler().add_new_object(self._update_status, self, run_in=0)

			# update loop
			interval = Scheduler().get_ticks(3)
			# use session random to keep it synchronised in mp games,
			# to be safe in case get_status_icon calls anything that changes anything
			run_in = self.session.random.randint(1, interval) # don't update all at once
			Scheduler().add_new_object(self._update_status, self, run_in=run_in, loops=-1,
				                         loop_interval = interval)

		# status icons, that are expensive to decide, can be appended/removed here
		self._registered_status_icons = []

	@property
	def fife_instance(self):
		return self._instance

	def save(self, db):
		super(ConcreteObject, self).save(db)
		db("INSERT INTO concrete_object(id, action_runtime) VALUES(?, ?)", self.worldid, \
			 self._instance.getActionRuntime())

	def load(self, db, worldid):
		super(ConcreteObject, self).load(db, worldid)
		self.__init()
		runtime = db.get_concrete_object_action_runtime(worldid)
		# delay setting of runtime until load of sub/super-class has set the action
		def set_action_runtime(self, runtime):
			# workaround to delay resolution of self._instance, which doesn't exist yet
			self._instance.setActionRuntime(runtime)
		Scheduler().add_new_object( Callback(set_action_runtime, self, runtime), self, run_in=0)

	def act(self, action, facing_loc=None, repeating=False):
		if not self.has_action(action):
			action = 'idle'
		# TODO This should not happen, this is a fix for the component introduction
		# Should be fixed as soon as we move concrete object to a component as well
		# which ensures proper initialization order for loading and initing
		if self._instance is not None:
			if facing_loc is None:
				facing_loc = self._instance.getFacingLocation()
			UnitClass.ensure_action_loaded(self._action_set_id, action) # lazy
			self._instance.act(action+"_"+str(self._action_set_id), facing_loc, repeating)
		self._action = action

	def has_action(self, action):
		"""Checks if this unit has a certain action.
		@param anim: animation id as string"""
		return (action in ActionSetLoader.get_sets()[self._action_set_id])

	def remove(self):
		self._remove_status_icon()
		self._instance.getLocationRef().getLayer().deleteInstance(self._instance)
		self._instance = None
		Scheduler().rem_all_classinst_calls(self)
		super(ConcreteObject, self).remove()

	def show_menu(self, jump_to_tabclass=None):
		"""Shows tabs from self.__class__.tabs, if there are any.
		@param jump_to_tabclass: open the first tab that is a subclass to this parameter
		"""
		# this local import prevents circular imports
		from horizons.gui.tabs import TabWidget
		tablist = None
		if self.owner == self.session.world.player:
			tablist = self.__class__.tabs
		else: # this is an enemy instance with respect to the local player
			tablist = self.__class__.enemy_tabs

		if tablist:
			tabs = [ tabclass(self) for tabclass in tablist if tabclass.shown_for(self) ]
			tabwidget = TabWidget(self.session.ingame_gui, tabs=tabs)

			if jump_to_tabclass:
				num = None
				for i in xrange( len(tabs) ):
					if isinstance(tabs[i], jump_to_tabclass):
						num = i
						break
				if num is not None:
					tabwidget._show_tab(num)

			self.session.ingame_gui.show_menu( tabwidget )

	def get_status_icons(self):
		"""Returns a list of StatusIcon instances"""
		return self._registered_status_icons[:] # always add pushed icons

	def _update_status(self):
		"""Handles status icon bar"""
		status_list = self.get_status_icons()

		if hasattr(self, "_old_status_list"):
			if status_list == self._old_status_list:
				return
		self._old_status_list = status_list

		self._remove_status_icon()

		if status_list:
			status = max(status_list, key=StatusIcon.get_sorting_key())

			# draw
			rel = fife.Point(8, -8) # TODO: find suitable place within instance
			# NOTE: rel is interpreted as pixel offset on screen
			node = fife.RendererNode(self.fife_instance, rel)
			status.render(self._status_icon_renderer, self._status_icon_key, node)

	def _remove_status_icon(self):
		self._status_icon_renderer.removeAll(self._status_icon_key)

	@classmethod
	def get_random_action_set(cls, level=0, exact_level=False):
		"""Returns an action set for an object of type object_id in a level <= the specified level.
		The highest level number is preferred.
		@param db: UhDbAccessor
		@param object_id: type id of building
		@param level: level to prefer. a lower level might be chosen
		@param exact_level: choose only action sets from this level. return val might be None here.
		@return: tuple: (action_set_id, preview_action_set_id)"""
		assert level >= 0

		action_sets_by_lvl = cls.action_sets_by_level
		action_sets = cls.action_sets
		action_set = None
		preview = None
		if exact_level:
			action_set = action_sets_by_lvl[level][randint(0, len(action_sets_by_lvl[level])-1)] if len(action_sets_by_lvl[level]) > 0 else None
		else: # search all levels for an action set, starting with highest one
			for possible_level in reversed(xrange(level+1)):
				if len(action_sets_by_lvl[possible_level]) > 0:
					action_set = action_sets_by_lvl[possible_level][randint(0, len(action_sets_by_lvl[possible_level])-1)]
					break
			if action_set is None:
				assert False, "Couldn't find action set for obj %s(%s) in lvl %s" % (cls.id, cls.name, level)

		if action_set is not None and 'preview' in action_sets[action_set]:
			preview = action_sets[action_set]['preview']
		return (action_set, preview)
